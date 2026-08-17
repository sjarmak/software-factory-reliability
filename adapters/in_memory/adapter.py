"""Protocol adapter over the in-memory factory.

Importable API: construct InMemoryAdapter and call handle(command) with
dicts shaped like adapters/protocol.schema.json commands; each call returns
one observation dict. An optional stdio loop (python3 -m
adapters.in_memory.adapter) reads one command per line and writes one
observation per line.

Every command is validated against the schema before dispatch, so a driver
that speaks the protocol against this adapter has also exercised the
message contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

from adapters.in_memory.model import DRIFTED_STATE, TERMINAL_OUTCOMES, Factory

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "protocol.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_COMMAND_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/command", "$defs": _SCHEMA["$defs"]}
)

WORK_ID = "w-1"
EFFECT_ID = "eff-1"
PRIOR_EFFECT_ID = "eff-0"
REQUEST_ID = "req-1"
VIEW_ID = "view-1"
CHECK_ID = "chk-1"
RESOURCE_ID = "res-1"
REQUIRED_STATE = "owned-and-private"
CHILD_IDS = ("c-1", "c-2", "c-3")
STRAGGLER_ID = "c-3"
MODES = ("protected", "unsafe")
SCENARIOS = (
    "worker-dies-agent-survives",
    "stale-writer-completes",
    "effect-commits-ack-is-lost",
    "event-is-lost",
    "child-completes-after-join",
    "request-accepted-effect-never-applied",
    "source-advances-view-answers-anyway",
    "state-changes-check-does-not",
    "guard-refuses-repair-never-runs",
)


class AdapterError(Exception):
    """Raised by op handlers; surfaced as an ok=false observation."""


def _script_worker_dies(f: Factory, mode: str) -> list:
    """Attempt 1 claims the work and launches a session; the worker dies at
    the barrier while the session survives; attempt 2 either reattaches to
    the surviving session (protected) or launches a second session against
    the same worktree (unsafe)."""

    def claim_and_launch():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)

    def retry_attempt_2():
        f.start_worker("worker-2")
        survivors = [s for s in f.sessions.values()
                     if s.alive and s.work_id == WORK_ID]
        if mode == "protected" and survivors:
            f.attach_session(WORK_ID, survivors[0].session_id, attempt=2)
        else:
            f.launch_session(WORK_ID, "worker-2", attempt=2)

    def complete():
        f.complete_work(WORK_ID, f.attempt_sessions[2], attempt=2)

    return [
        ("action", "claim-and-launch", claim_and_launch),
        ("barrier", "session-running", None),
        ("action", "retry-attempt-2", retry_attempt_2),
        ("action", "complete", complete),
        ("barrier", "run-complete", None),
    ]


def _script_stale_writer(f: Factory, mode: str) -> list:
    """The generation-7 writer prepares its artifact and pauses before
    publication; generation 8 takes ownership and publishes; the stale
    generation-7 writer then attempts publication. Fenced publication
    compares generations at the destination; unfenced publication checks
    the caller's stale observation and then writes."""
    state: dict = {}

    def g7_prepare():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        artifact_id, observed = f.prepare_artifact(
            WORK_ID, 7, f.attempt_sessions[1])
        state["g7_artifact"] = artifact_id
        state["g7_observed"] = observed

    def g8_publish():
        f.start_worker("worker-2")
        f.launch_session(WORK_ID, "worker-2", attempt=2)
        generation = f.work_items[WORK_ID].generation
        artifact_id, observed = f.prepare_artifact(
            WORK_ID, generation, f.attempt_sessions[2])
        f.publish(WORK_ID, artifact_id, generation, f.attempt_sessions[2],
                  observed_generation=observed)
        f.complete_work(WORK_ID, f.attempt_sessions[2], attempt=2)

    def g7_stale_publish():
        f.publish(WORK_ID, state["g7_artifact"], 7, f.attempt_sessions[1],
                  observed_generation=state["g7_observed"])

    def g7_stale_complete():
        f.complete_work(WORK_ID, f.attempt_sessions[1], attempt=1,
                        generation=7)

    return [
        ("action", "g7-prepare", g7_prepare),
        ("barrier", "before-publication", None),
        ("action", "g8-publish", g8_publish),
        ("action", "g7-stale-publish", g7_stale_publish),
        ("action", "g7-stale-complete", g7_stale_complete),
        ("barrier", "run-complete", None),
    ]


def _script_effect_ack(f: Factory, mode: str) -> list:
    """Attempt 1 commits the effect at the destination; the acknowledgement
    is lost on the bus; attempt 2 retries the same effect identity. A dedup
    destination returns the existing mutation; a semantics-free destination
    applies a second one."""
    state: dict = {}

    def attempt_1_commit():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        mutation, _created = f.apply_effect(
            WORK_ID, f.attempt_sessions[1], EFFECT_ID, "payload-1", attempt=1)
        state["receipt_1"] = mutation.receipt

    def attempt_1_ack():
        def apply():
            f.effect_ledger[EFFECT_ID] = {
                "status": "acknowledged",
                "receipt": state["receipt_1"],
                "attempt": 1,
            }
        state["ack_1"] = f.emit_event(
            "ack", apply, effect_id=EFFECT_ID, attempt=1)

    def retry_attempt_2():
        if state.get("ack_1"):
            f.log("retry-not-needed", effect_id=EFFECT_ID)
        else:
            f.attach_session(WORK_ID, f.attempt_sessions[1], attempt=2)
            mutation, _created = f.apply_effect(
                WORK_ID, f.attempt_sessions[2], EFFECT_ID, "payload-1",
                attempt=2)

            def apply():
                f.effect_ledger[EFFECT_ID] = {
                    "status": "acknowledged",
                    "receipt": mutation.receipt,
                    "attempt": 2,
                }
            f.emit_event("ack", apply, effect_id=EFFECT_ID, attempt=2)
        f.complete_work(WORK_ID, f.attempt_sessions[1], attempt=2)

    return [
        ("action", "attempt-1-commit", attempt_1_commit),
        ("barrier", "effect-committed-ack-pending", None),
        ("action", "attempt-1-ack", attempt_1_ack),
        ("action", "retry-attempt-2", retry_attempt_2),
        ("barrier", "run-complete", None),
    ]


def _script_event_lost(f: Factory, mode: str) -> list:
    """The effect commits at the destination; the ledger-update event is
    dropped. The protected wiring runs a reconciler that reads destination
    state and repairs the ledger; the unsafe wiring is event-only and the
    ledger stays stale."""
    state: dict = {}

    def apply_effect():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        mutation, _created = f.apply_effect(
            WORK_ID, f.attempt_sessions[1], EFFECT_ID, "payload-1", attempt=1)
        state["receipt"] = mutation.receipt

    def emit_ledger_event():
        def apply():
            f.effect_ledger[EFFECT_ID] = {
                "status": "recorded",
                "receipt": state["receipt"],
            }
            f.work_items[WORK_ID].status = "completed"
        f.emit_event("ledger-update", apply, effect_id=EFFECT_ID)

    def reconcile_step():
        if f.reconciler_enabled:
            f.reconcile(WORK_ID)
        else:
            f.log("reconciler-absent", work_id=WORK_ID)

    return [
        ("action", "apply-effect", apply_effect),
        ("barrier", "before-ledger-event", None),
        ("action", "emit-ledger-event", emit_ledger_event),
        ("action", "reconcile", reconcile_step),
        ("barrier", "run-complete", None),
    ]


def _script_child_after_join(f: Factory, mode: str) -> list:
    """The coordinator fans work out to three children. One child's lease
    expires and it is dispositioned blocked, so the join fires over the two
    that published. The written-off child then finishes and writes into the
    merge slot. The protected wiring fences that slot on the parent's join
    generation and recomputes the fold from the durable child records; the
    unsafe wiring checks the child's own stale observation and never
    recomputes."""
    state: dict = {}

    def fan_out():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        for index, child in enumerate(CHILD_IDS):
            attempt = index + 2
            worker = f"worker-{attempt}"
            f.start_worker(worker)
            f.claim_work(child, attempt=attempt)
            f.launch_session(child, worker, attempt=attempt)

    def children_publish():
        for index, child in enumerate(CHILD_IDS):
            if child == STRAGGLER_ID:
                continue
            attempt = index + 2
            session = f.attempt_sessions[attempt]
            f.apply_effect(child, session, f"eff-{child}", f"payload-{child}",
                           attempt=attempt)
            f.complete_work(child, session, attempt=attempt, generation=1)

    def straggler_prepares():
        # The straggler computes its write into the merge slot before the join
        # fires, so its observation of the destination is empty and stays
        # empty. That stale observation is what the unfenced path checks.
        artifact_id, observed = f.prepare_merge(
            WORK_ID, [STRAGGLER_ID], 1, f.attempt_sessions[4])
        state["straggler_artifact"] = artifact_id
        state["straggler_observed"] = observed

    def _join(label: str):
        dispositions = f.derive_dispositions(list(CHILD_IDS))
        undispositioned = sorted(child for child, d in dispositions.items()
                                 if d == "undispositioned")
        if undispositioned:
            f.log("join-incomplete", work_id=WORK_ID,
                  undispositioned=undispositioned)
            return
        folded = sorted(child for child, d in dispositions.items()
                        if d == "published")
        generation = f.work_items[WORK_ID].generation
        artifact_id, observed = f.prepare_merge(
            WORK_ID, folded, generation, f.attempt_sessions[1])
        f.publish(WORK_ID, artifact_id, generation, f.attempt_sessions[1],
                  observed_generation=observed)
        f.log(label, work_id=WORK_ID, artifact_id=artifact_id,
              folded_children=folded, generation=generation,
              dispositions=dispositions)

    def join_fires():
        _join("join-published")

    def straggler_returns():
        session = f.attempt_sessions[4]
        f.apply_effect(STRAGGLER_ID, session, f"eff-{STRAGGLER_ID}",
                       f"payload-{STRAGGLER_ID}", attempt=4)
        f.complete_work(STRAGGLER_ID, session, attempt=4, generation=1)
        f.publish(WORK_ID, state["straggler_artifact"], 1, session,
                  observed_generation=state["straggler_observed"])

    def recompute_join():
        if not f.reconciler_enabled:
            f.log("join-recompute-absent", work_id=WORK_ID)
            return
        _join("join-recomputed")

    return [
        ("action", "fan-out", fan_out),
        ("action", "children-publish", children_publish),
        ("action", "straggler-prepares", straggler_prepares),
        ("barrier", "before-join", None),
        ("action", "join-fires", join_fires),
        ("action", "straggler-returns", straggler_returns),
        ("action", "recompute-join", recompute_join),
        ("barrier", "run-complete", None),
    ]


def _script_request_never_applied(f: Factory, mode: str) -> list:
    """The boundary durably accepts a request, the application leg is
    dropped, and the destination never receives the mutation. The protected
    boundary classifies by reading the destination back and reports
    requested-and-queued, then writes a terminal record when the window
    closes with the effect still absent. The unsafe boundary classifies from
    its own accepted dispatch, reports requested-and-applied, and records
    nothing."""

    def submit_request():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        f.accept_request(REQUEST_ID, WORK_ID, [EFFECT_ID])

    def application_leg():
        session = f.attempt_sessions[1]

        def apply():
            f.apply_effect(WORK_ID, session, EFFECT_ID, "payload-1", attempt=1)
        f.emit_event("apply-effect", apply, request_id=REQUEST_ID,
                     effect_id=EFFECT_ID)

    def report_outcome():
        if mode == "protected":
            f.classify_by_readback(REQUEST_ID)
        else:
            f.classify_by_dispatch(REQUEST_ID)

    def close_window():
        # The sweep that resolves accepted requests. Its absence is what
        # lets a returned outcome expire with nothing on record.
        if not f.reconciler_enabled:
            f.log("request-sweep-absent", request_id=REQUEST_ID)
            return
        request = f.requests[REQUEST_ID]
        if request.outcome in TERMINAL_OUTCOMES:
            f.record_request_terminal(REQUEST_ID, request.outcome,
                                      "the returned outcome was terminal")
            return
        f.record_request_terminal(
            REQUEST_ID, "expired",
            "accepted request still unapplied when the window closed")

    return [
        ("action", "submit-request", submit_request),
        ("barrier", "request-accepted", None),
        ("action", "application-leg", application_leg),
        ("action", "report-outcome", report_outcome),
        ("action", "close-window", close_window),
        ("barrier", "run-complete", None),
    ]


def _script_view_lag(f: Factory, mode: str) -> list:
    """The view consumes the source's first record and is current at the
    barrier. The source then advances and the index-update carrying that
    record is dropped, so the view holds a strict prefix of the source. The
    protected reader compares the view's published position against the
    source and refuses to answer an exact lookup it cannot answer freshly;
    the unsafe reader answers from the index alone and returns an empty
    result set as a successful absence. The two health surfaces differ the
    same way: one recomputes the lag, the other reports that the indexer
    ran."""

    def seed_and_index():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        f.apply_effect(WORK_ID, f.attempt_sessions[1], PRIOR_EFFECT_ID,
                       "payload-0", attempt=1)

        def apply():
            f.index_view_entry(VIEW_ID, PRIOR_EFFECT_ID)
        f.emit_event("index-update", apply, view_id=VIEW_ID,
                     effect_id=PRIOR_EFFECT_ID)

    def source_write():
        f.apply_effect(WORK_ID, f.attempt_sessions[1], EFFECT_ID,
                       "payload-1", attempt=1)

    def index_update():
        def apply():
            f.index_view_entry(VIEW_ID, EFFECT_ID)
        f.emit_event("index-update", apply, view_id=VIEW_ID,
                     effect_id=EFFECT_ID)

    def query_view():
        if mode == "protected":
            f.query_view_lag_checked(VIEW_ID, EFFECT_ID)
        else:
            f.query_view_index_only(VIEW_ID, EFFECT_ID)

    def report_health():
        if mode == "protected":
            f.view_health_by_position(VIEW_ID)
        else:
            f.view_health_by_liveness(VIEW_ID)

    return [
        ("action", "seed-and-index", seed_and_index),
        ("barrier", "view-current", None),
        ("action", "source-write", source_write),
        ("action", "index-update", index_update),
        ("action", "query-view", query_view),
        ("action", "report-health", report_health),
        ("barrier", "run-complete", None),
    ]


def _script_check_falsifiability(f: Factory, mode: str) -> list:
    """A check is registered over one destination record, the write that
    would satisfy it is dropped, and the check is evaluated twice: once
    while the record is missing and once after a retry puts it there. The
    state crosses the check's claim between the two evaluations, so a check
    that reports the state has to answer differently the second time. The
    protected check reads the destination back and does; the unsafe check
    stamps its own metadata key and reads that, so it answers pass in both
    states and its fail branch is never reachable."""

    def register_check():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        f.add_check(CHECK_ID, f"destination holds {EFFECT_ID}", EFFECT_ID)

    def attempt_application():
        def apply():
            f.apply_effect(WORK_ID, f.attempt_sessions[1], EFFECT_ID,
                           "payload-1", attempt=1)
        f.emit_event("effect-application", apply, work_id=WORK_ID,
                     effect_id=EFFECT_ID, attempt=1)

    def evaluate():
        if mode == "protected":
            f.check_by_destination_readback(CHECK_ID)
        else:
            f.check_by_self_written_verdict(CHECK_ID)

    def repair_application():
        f.apply_effect(WORK_ID, f.attempt_sessions[1], EFFECT_ID,
                       "payload-1", attempt=2)

    return [
        ("action", "register-check", register_check),
        ("barrier", "check-registered", None),
        ("action", "attempt-application", attempt_application),
        ("action", "evaluate-before-repair", evaluate),
        ("action", "repair-application", repair_application),
        ("action", "evaluate-after-repair", evaluate),
        ("barrier", "run-complete", None),
    ]


def _script_guard_before_repair(f: Factory, mode: str) -> list:
    """One guarded write lands while the resource conforms, the resource is
    then drifted out of the state that write requires, and the same write is
    attempted twice more. Both arms hold the same guard and the same repair;
    they differ only in which runs first. The protected arm reports the
    anomaly, repairs the resource, verifies it, and writes. The unsafe arm
    refuses on the precondition and returns, so its repair step is never
    reached and the second attempt fails exactly as the first did."""

    def seed_and_write():
        f.claim_work(WORK_ID, attempt=1)
        f.launch_session(WORK_ID, "worker-1", attempt=1)
        _guarded_write(f, mode, PRIOR_EFFECT_ID, "payload-0", attempt=1)

    def attempt_1():
        _guarded_write(f, mode, EFFECT_ID, "payload-1", attempt=1)

    def attempt_2():
        _guarded_write(f, mode, EFFECT_ID, "payload-1", attempt=2)

    return [
        ("action", "seed-and-write", seed_and_write),
        ("barrier", "resource-conforming", None),
        ("action", "attempt-1", attempt_1),
        ("action", "attempt-2", attempt_2),
        ("barrier", "run-complete", None),
    ]


def _guarded_write(f: Factory, mode: str, effect_id: str, payload: str,
                   attempt: int) -> dict:
    write = (f.guarded_write_repair_first if mode == "protected"
             else f.guarded_write_check_first)
    return write(RESOURCE_ID, WORK_ID, f.attempt_sessions[1], effect_id,
                 payload, attempt)


_SCRIPT_BUILDERS = {
    "worker-dies-agent-survives": _script_worker_dies,
    "stale-writer-completes": _script_stale_writer,
    "effect-commits-ack-is-lost": _script_effect_ack,
    "event-is-lost": _script_event_lost,
    "child-completes-after-join": _script_child_after_join,
    "request-accepted-effect-never-applied": _script_request_never_applied,
    "source-advances-view-answers-anyway": _script_view_lag,
    "state-changes-check-does-not": _script_check_falsifiability,
    "guard-refuses-repair-never-runs": _script_guard_before_repair,
}


class InMemoryAdapter:
    """Implements the protocol ops against the in-memory Factory."""

    def __init__(self) -> None:
        self.factory: Factory | None = None
        self.scenario: str | None = None
        self.mode: str | None = None
        self.script: list = []
        self.pos = 0
        self.started = False
        self.paused_at: str | None = None
        self.barriers_reached: list[str] = []
        self.faults: list[dict] = []
        self.run_complete = False

    def handle(self, command) -> dict:
        errors = sorted(_COMMAND_VALIDATOR.iter_errors(command), key=str)
        if errors:
            op = command.get("op") if isinstance(command, dict) else None
            op = op if isinstance(op, str) else "invalid"
            return {"op": op, "ok": False,
                    "error": f"invalid command: {errors[0].message}"}
        op = command["op"]
        params = command.get("params", {})
        try:
            data = getattr(self, "_op_" + op)(params)
        except AdapterError as exc:
            return {"op": op, "ok": False, "error": str(exc)}
        return {"op": op, "ok": True, "data": data}

    def _factory_or_error(self) -> Factory:
        if self.factory is None:
            raise AdapterError("no scenario seeded; send seed first")
        return self.factory

    # ops

    def _op_seed(self, params: dict) -> dict:
        scenario = params["scenario"]
        mode = params["mode"]
        if scenario not in SCENARIOS:
            raise AdapterError(f"unknown scenario: {scenario!r}")
        semantics = "dedup"
        if scenario == "effect-commits-ack-is-lost" and mode == "unsafe":
            semantics = "none"
        publisher_mode = "fenced" if mode == "protected" else "unfenced"
        reconciler_enabled = mode == "protected"
        f = Factory(semantics, publisher_mode, reconciler_enabled)
        generation = 7 if scenario == "stale-writer-completes" else 1
        f.add_work(WORK_ID, generation=generation)
        if scenario == "child-completes-after-join":
            for child in CHILD_IDS:
                f.add_work(child, generation=1)
        if scenario == "source-advances-view-answers-anyway":
            # An exact lookup for a named record is a read-your-writes
            # query, so the view's declared contract for it is zero lag.
            f.add_view(VIEW_ID, max_lag=0)
        if scenario == "guard-refuses-repair-never-runs":
            # The resource starts in the state the guarded write requires, so
            # the run has a conforming write on record before the drift and
            # both arms are provably identical up to that point.
            f.add_resource(RESOURCE_ID, REQUIRED_STATE)
        f.start_worker("worker-1")
        f.log("seeded", scenario=scenario, mode=mode)
        self.factory = f
        self.scenario = scenario
        self.mode = mode
        self.script = _SCRIPT_BUILDERS[scenario](f, mode)
        self.pos = 0
        self.started = False
        self.paused_at = None
        self.barriers_reached = []
        self.faults = []
        self.run_complete = False
        return {
            "scenario": scenario,
            "mode": mode,
            "destination_semantics": semantics,
            "publisher_mode": publisher_mode,
            "reconciler_enabled": reconciler_enabled,
        }

    def _op_start(self, params: dict) -> dict:
        f = self._factory_or_error()
        if self.started:
            raise AdapterError("run already started")
        self.started = True
        f.log("run-started", scenario=self.scenario, mode=self.mode)
        return {"started": True, "tick": f.tick}

    def _op_wait_for_barrier(self, params: dict) -> dict:
        name = params["name"]
        f = self._factory_or_error()
        if not self.started:
            raise AdapterError("send start before wait_for_barrier")
        self.paused_at = None
        while self.pos < len(self.script):
            kind, label, fn = self.script[self.pos]
            self.pos += 1
            if kind == "action":
                fn()
                continue
            self.barriers_reached.append(label)
            f.log("barrier-reached", barrier=label)
            if label == "run-complete":
                self.run_complete = True
            if label == name:
                self.paused_at = label
                return {"barrier": label, "tick": f.tick}
        raise AdapterError(f"barrier {name!r} not reached; script exhausted")

    def _op_inject_fault(self, params: dict) -> dict:
        kind = params["kind"]
        at_barrier = params["at_barrier"]
        f = self._factory_or_error()
        if self.paused_at != at_barrier:
            raise AdapterError(
                f"not paused at barrier {at_barrier!r} "
                f"(paused at {self.paused_at!r})")
        f.log("fault-injected", fault_kind=kind, at_barrier=at_barrier)
        if kind == "kill_worker":
            f.kill_worker(params.get("target", "worker-1"))
        elif kind == "drop_event":
            f.bus.arm_drop()
            f.log("drop-armed", pending_drops=f.bus.drop_remaining)
        elif kind == "expire_lease":
            f.expire_lease(params.get("target", WORK_ID))
        elif kind == "drift_resource":
            f.drift_resource(params.get("target", RESOURCE_ID), DRIFTED_STATE)
        record = {"kind": kind, "at_barrier": at_barrier}
        if "target" in params:
            record["target"] = params["target"]
        self.faults.append(record)
        return record

    def _op_advance_generation(self, params: dict) -> dict:
        f = self._factory_or_error()
        work_id = params["work_id"]
        if work_id not in f.work_items:
            raise AdapterError(f"unknown work_id: {work_id!r}")
        generation = f.advance_generation(work_id)
        return {"work_id": work_id, "generation": generation}

    def _op_read_authoritative_state(self, params: dict) -> dict:
        return self._factory_or_error().state_snapshot()

    def _op_read_external_effects(self, params: dict) -> dict:
        return self._factory_or_error().effects_snapshot()

    def _op_read_running_executors(self, params: dict) -> dict:
        return self._factory_or_error().executors_snapshot()

    def _op_read_campaign_coverage(self, params: dict) -> dict:
        self._factory_or_error()
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "barriers_reached": list(self.barriers_reached),
            "faults_injected": list(self.faults),
            "run_complete": self.run_complete,
        }

    def _op_collect_evidence(self, params: dict) -> dict:
        f = self._factory_or_error()
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "barriers_reached": list(self.barriers_reached),
            "faults": list(self.faults),
            "events": [dict(e) for e in f.events],
            "identities": f.identity_snapshot(),
            "authoritative_state": f.state_snapshot(),
            "external_effects": f.effects_snapshot(),
            "running_executors": f.executors_snapshot(),
        }


def main() -> int:
    """Stdio loop: one JSON command per line in, one observation per line out."""
    adapter = InMemoryAdapter()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except json.JSONDecodeError as exc:
            observation = {"op": "invalid", "ok": False,
                           "error": f"not JSON: {exc}"}
        else:
            observation = adapter.handle(command)
        sys.stdout.write(json.dumps(observation, sort_keys=True) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
