"""Run one reliability drill against the in-memory reference factory.

Usage:
    python3 -m adapters.in_memory.run_drill <drill> --mode protected|unsafe
        [--out-dir out/evidence]

Drills: worker-dies-agent-survives, stale-writer-completes,
effect-commits-ack-is-lost, event-is-lost.

Exit codes:
    0  protected mode and the oracle passed
    2  unsafe mode and the oracle detected the expected violation
    1  anything else, with a diagnostic on stderr

Both modes write out/evidence/<drill>-<mode>.json containing the scenario,
the barrier, the fault, the per-tick event log, the oracle verdict, and the
identity mappings.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from adapters.in_memory.adapter import EFFECT_ID, WORK_ID, InMemoryAdapter


class DrillError(Exception):
    """A protocol op failed while driving the drill."""


def _first_tick(events: list, kind: str, **match) -> int | None:
    """Tick of the first event of this kind whose fields match, else None."""
    for event in events:
        if event.get("kind") != kind:
            continue
        if all(event.get(k) == v for k, v in match.items()):
            return event["tick"]
    return None


def _preconditions_failed(missing: list, name: str, description: str) -> dict:
    return {
        "name": name,
        "description": description,
        "expected": "drill preconditions observed in the event log",
        "observed": {"missing_preconditions": missing},
        "detail": {},
        "verdict": "error",
    }


def _oracle_worker_dies(evidence: dict) -> dict:
    """One session per claim after retry. The verdict only counts if the
    event log shows the fault and the retry actually happened: a worker
    death with a surviving session, then a second attempt. Without those
    preconditions a single bound session proves nothing."""
    events = evidence["events"]
    state = evidence["authoritative_state"]
    description = ("A worker died leaving a surviving session, a retry ran, "
                   "and exactly one session is bound to the claim.")
    missing = []
    kill_tick = None
    for event in events:
        if event.get("kind") == "worker-killed" and event.get("surviving_sessions"):
            kill_tick = event["tick"]
            break
    if kill_tick is None:
        missing.append("worker-killed with a surviving session")
    retry_tick = None
    for kind in ("session-attached", "session-launched"):
        tick = _first_tick(events, kind, attempt=2)
        if tick is not None and (retry_tick is None or tick < retry_tick):
            retry_tick = tick
    if retry_tick is None or (kill_tick is not None and retry_tick <= kill_tick):
        missing.append("attempt-2 launch or attach after the worker death")
    if _first_tick(events, "outcome-accepted") is None:
        missing.append("an accepted outcome (the run finished its work)")
    if missing:
        return _preconditions_failed(missing, "one-session-per-claim", description)
    claim = state["claims"].get(WORK_ID)
    bound = claim["session_ids"] if claim else []
    observed = len(bound)
    verdict = "pass" if observed == 1 else ("violation" if observed > 1 else "error")
    return {
        "name": "one-session-per-claim",
        "description": description,
        "expected": 1,
        "observed": observed,
        "detail": {"session_ids": bound, "kill_tick": kill_tick,
                   "retry_tick": retry_tick},
        "verdict": verdict,
    }


def _oracle_stale_writer(evidence: dict) -> dict:
    """The stale generation must actually return and act after replacement,
    and both its publication and its completion must be rejected. A current
    artifact with no stale attempt on record is not a pass."""
    events = evidence["events"]
    effects = evidence["external_effects"]
    description = ("After ownership advanced to generation 8 and generation 8 "
                   "published, the returning generation-7 writer attempted "
                   "publication and completion; the destination holds the "
                   "generation-8 artifact and both stale attempts were "
                   "rejected.")
    missing = []
    if _first_tick(events, "lease-expired") is None:
        missing.append("the injected lease expiry")
    if _first_tick(events, "generation-advanced", generation=8) is None:
        missing.append("ownership advancing to generation 8")
    g8_tick = None
    for kind in ("publish-accepted", "publish-unfenced-applied"):
        tick = _first_tick(events, kind, generation=8)
        if tick is not None:
            g8_tick = tick
            break
    if g8_tick is None:
        missing.append("generation 8 publishing")
    g7_publish_kind = None
    g7_publish_tick = None
    for kind in ("publish-rejected-stale", "publish-unfenced-applied",
                 "publish-unfenced-skipped", "publish-accepted"):
        tick = _first_tick(events, kind, generation=7)
        if tick is not None and g8_tick is not None and tick > g8_tick:
            g7_publish_kind = kind
            g7_publish_tick = tick
            break
    if g7_publish_tick is None:
        missing.append("a stale generation-7 publish attempt after generation 8")
    g7_complete_kind = None
    for kind in ("completion-rejected-stale", "outcome-accepted"):
        tick = _first_tick(events, kind, generation=7)
        if tick is not None:
            g7_complete_kind = kind
            break
    if g7_complete_kind is None:
        missing.append("a stale generation-7 completion attempt")
    if missing:
        return _preconditions_failed(
            missing, "stale-writer-rejected-at-destination", description)
    artifact = effects["artifact"]
    artifact_id = artifact["artifact_id"] if artifact else None
    held = (artifact_id == "artifact-g8"
            and g7_publish_kind in ("publish-rejected-stale",
                                    "publish-unfenced-skipped")
            and g7_complete_kind == "completion-rejected-stale")
    breached = (artifact_id == "artifact-g7"
                or g7_publish_kind in ("publish-unfenced-applied",
                                       "publish-accepted")
                or g7_complete_kind == "outcome-accepted")
    verdict = "pass" if held else ("violation" if breached else "error")
    return {
        "name": "stale-writer-rejected-at-destination",
        "description": description,
        "expected": {"artifact": "artifact-g8",
                     "stale_publish": "publish-rejected-stale",
                     "stale_completion": "completion-rejected-stale"},
        "observed": {"artifact": artifact_id,
                     "stale_publish": g7_publish_kind,
                     "stale_completion": g7_complete_kind},
        "detail": {"g8_publish_tick": g8_tick,
                   "g7_publish_tick": g7_publish_tick},
        "verdict": verdict,
    }


def _oracle_effect_ack(evidence: dict) -> dict:
    """One destination mutation per effect identity, and only after the log
    shows the acknowledgement was actually lost and a retry actually sent
    the same effect identity again."""
    events = evidence["events"]
    effects = evidence["external_effects"]
    description = ("The acknowledgement was dropped, a second attempt "
                   "re-sent the effect identity, and the destination holds "
                   "exactly one mutation for it.")
    missing = []
    drop_tick = _first_tick(events, "ack-dropped")
    if drop_tick is None:
        missing.append("the dropped acknowledgement")
    retry_tick = None
    for kind in ("effect-applied", "effect-deduplicated"):
        tick = _first_tick(events, kind, attempt=2)
        if tick is not None:
            retry_tick = tick
            break
    if retry_tick is None or (drop_tick is not None and retry_tick <= drop_tick):
        missing.append("an attempt-2 retry of the effect after the drop")
    if missing:
        return _preconditions_failed(
            missing, "one-mutation-per-effect-identity", description)
    mutations = [m for m in effects["mutations"] if m["effect_id"] == EFFECT_ID]
    observed = len(mutations)
    verdict = "pass" if observed == 1 else ("violation" if observed > 1 else "error")
    return {
        "name": "one-mutation-per-effect-identity",
        "description": description,
        "expected": 1,
        "observed": observed,
        "detail": {"mutations": mutations, "drop_tick": drop_tick,
                   "retry_tick": retry_tick},
        "verdict": verdict,
    }


def _oracle_event_lost(evidence: dict) -> dict:
    """Ledger converges with the destination, and only after the log shows
    the ledger-update event was actually dropped and a reconciliation pass
    (or its documented absence) actually ran."""
    events = evidence["events"]
    state = evidence["authoritative_state"]
    effects = evidence["external_effects"]
    description = ("The ledger-update event was dropped; reconciliation ran "
                   "(or was absent by configuration); the ledger holds the "
                   "destination's receipt and the work item is completed.")
    missing = []
    drop_tick = _first_tick(events, "ledger-update-dropped")
    if drop_tick is None:
        missing.append("the dropped ledger-update event")
    repair_tick = None
    for kind in ("reconciled", "reconciler-absent"):
        tick = _first_tick(events, kind)
        if tick is not None:
            repair_tick = tick
            break
    if repair_tick is None or (drop_tick is not None and repair_tick <= drop_tick):
        missing.append("a reconciliation pass (or explicit absence) after the drop")
    if missing:
        return _preconditions_failed(
            missing, "ledger-matches-destination", description)
    receipts = [m["receipt"] for m in effects["mutations"]
                if m["effect_id"] == EFFECT_ID]
    ledger_record = state["effect_ledger"].get(EFFECT_ID)
    work_status = state["work_items"][WORK_ID]["status"]
    observed = {
        "destination_mutations": len(receipts),
        "ledger_record": ledger_record,
        "work_status": work_status,
    }
    if not receipts:
        verdict = "error"
    else:
        converged = (ledger_record is not None
                     and ledger_record.get("receipt") in receipts
                     and work_status == "completed")
        verdict = "pass" if converged else "violation"
    return {
        "name": "ledger-matches-destination",
        "description": description,
        "expected": {"ledger_has_receipt": True, "work_status": "completed"},
        "observed": observed,
        "detail": {"destination_receipts": receipts, "drop_tick": drop_tick,
                   "repair_tick": repair_tick},
        "verdict": verdict,
    }


@dataclass(frozen=True)
class DrillSpec:
    barrier: str
    fault_kind: str
    oracle: object
    post_fault_ops: tuple = field(default_factory=tuple)


DRILLS: dict[str, DrillSpec] = {
    "worker-dies-agent-survives": DrillSpec(
        barrier="session-running",
        fault_kind="kill_worker",
        oracle=_oracle_worker_dies,
    ),
    "stale-writer-completes": DrillSpec(
        barrier="before-publication",
        fault_kind="expire_lease",
        oracle=_oracle_stale_writer,
        post_fault_ops=(("advance_generation", {"work_id": WORK_ID}),),
    ),
    "effect-commits-ack-is-lost": DrillSpec(
        barrier="effect-committed-ack-pending",
        fault_kind="drop_event",
        oracle=_oracle_effect_ack,
    ),
    "event-is-lost": DrillSpec(
        barrier="before-ledger-event",
        fault_kind="drop_event",
        oracle=_oracle_event_lost,
    ),
}

MODES = ("protected", "unsafe")


def run_drill(drill: str, mode: str,
              out_dir: str | Path = "out/evidence") -> int:
    """Drive one drill through the protocol and evaluate its oracle.

    Returns the process exit code (0, 1, or 2); see the module docstring.
    """
    if drill not in DRILLS:
        print(f"unknown drill: {drill!r} (known: {', '.join(sorted(DRILLS))})",
              file=sys.stderr)
        return 1
    if mode not in MODES:
        print(f"unknown mode: {mode!r} (known: protected, unsafe)",
              file=sys.stderr)
        return 1

    spec = DRILLS[drill]
    adapter = InMemoryAdapter()

    def send(op: str, **params) -> dict:
        command: dict = {"op": op}
        if params:
            command["params"] = params
        observation = adapter.handle(command)
        if not observation["ok"]:
            raise DrillError(f"{op} failed: {observation['error']}")
        return observation["data"]

    try:
        send("seed", scenario=drill, mode=mode)
        send("start")
        send("wait_for_barrier", name=spec.barrier)
        send("inject_fault", kind=spec.fault_kind, at_barrier=spec.barrier)
        for op, params in spec.post_fault_ops:
            send(op, **params)
        send("wait_for_barrier", name="run-complete")
        state = send("read_authoritative_state")
        effects = send("read_external_effects")
        executors = send("read_running_executors")
        coverage = send("read_campaign_coverage")
        record = send("collect_evidence")
    except DrillError as exc:
        print(f"drill {drill} ({mode}): {exc}", file=sys.stderr)
        return 1

    evidence = {
        "scenario": drill,
        "mode": mode,
        "barrier": spec.barrier,
        "fault": {"kind": spec.fault_kind, "at_barrier": spec.barrier},
        "barriers_reached": coverage["barriers_reached"],
        "campaign_coverage": coverage,
        "events": record["events"],
        "identities": record["identities"],
        "authoritative_state": state,
        "external_effects": effects,
        "running_executors": executors,
    }
    oracle = spec.oracle(evidence)
    evidence["oracle"] = oracle
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    evidence_file = out_path / f"{drill}-{mode}.json"
    evidence_file.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    verdict = oracle["verdict"]
    if mode == "protected" and verdict == "pass":
        print(f"drill {drill} (protected): oracle pass; evidence {evidence_file}")
        return 0
    if mode == "unsafe" and verdict == "violation":
        print(f"drill {drill} (unsafe): oracle detected the expected "
              f"violation; evidence {evidence_file}")
        return 2
    print(f"drill {drill} ({mode}): unexpected oracle verdict {verdict!r}; "
          f"expected {'pass' if mode == 'protected' else 'violation'}; "
          f"oracle: {json.dumps(oracle, sort_keys=True)}",
          file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m adapters.in_memory.run_drill",
        description="Run one reliability drill against the in-memory "
                    "reference factory.")
    parser.add_argument("drill", choices=sorted(DRILLS))
    parser.add_argument("--mode", required=True, choices=list(MODES))
    parser.add_argument("--out-dir", default="out/evidence")
    args = parser.parse_args(argv)
    return run_drill(args.drill, args.mode, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
