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

from adapters.in_memory.model import Factory

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "protocol.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_COMMAND_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/command", "$defs": _SCHEMA["$defs"]}
)

WORK_ID = "w-1"
EFFECT_ID = "eff-1"
MODES = ("protected", "unsafe")
SCENARIOS = (
    "worker-dies-agent-survives",
    "stale-writer-completes",
    "effect-commits-ack-is-lost",
    "event-is-lost",
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

    return [
        ("action", "g7-prepare", g7_prepare),
        ("barrier", "before-publication", None),
        ("action", "g8-publish", g8_publish),
        ("action", "g7-stale-publish", g7_stale_publish),
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


_SCRIPT_BUILDERS = {
    "worker-dies-agent-survives": _script_worker_dies,
    "stale-writer-completes": _script_stale_writer,
    "effect-commits-ack-is-lost": _script_effect_ack,
    "event-is-lost": _script_event_lost,
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
        record = {"kind": kind, "at_barrier": at_barrier}
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
