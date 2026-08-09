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


def _oracle_worker_dies(state: dict, effects: dict) -> dict:
    """Counts sessions bound to the claim. One session per claim is the
    invariant; a second session against the same worktree is the violation."""
    claim = state["claims"].get(WORK_ID)
    bound = claim["session_ids"] if claim else []
    observed = len(bound)
    if observed == 1:
        verdict = "pass"
    elif observed > 1:
        verdict = "violation"
    else:
        verdict = "error"
    return {
        "name": "one-session-per-claim",
        "description": "Exactly one session is bound to the claim for the "
                       "work item after retry.",
        "expected": 1,
        "observed": observed,
        "detail": {"session_ids": bound},
        "verdict": verdict,
    }


def _oracle_stale_writer(state: dict, effects: dict) -> dict:
    """Compares the authoritative artifact at the destination to the stale
    generation-7 artifact."""
    artifact = effects["artifact"]
    current_id = "artifact-g8"
    stale_id = "artifact-g7"
    if artifact is None:
        verdict = "error"
    elif artifact["artifact_id"] == current_id:
        verdict = "pass"
    elif artifact["artifact_id"] == stale_id:
        verdict = "violation"
    else:
        verdict = "error"
    return {
        "name": "authoritative-artifact-is-current-generation",
        "description": "The published artifact at the destination is the "
                       "generation-8 artifact, not the stale generation-7 "
                       "artifact.",
        "expected": current_id,
        "observed": artifact["artifact_id"] if artifact else None,
        "detail": {"artifact": artifact, "stale_artifact_id": stale_id},
        "verdict": verdict,
    }


def _oracle_effect_ack(state: dict, effects: dict) -> dict:
    """Counts destination mutations carrying the effect identity."""
    mutations = [m for m in effects["mutations"]
                 if m["effect_id"] == EFFECT_ID]
    observed = len(mutations)
    if observed == 1:
        verdict = "pass"
    elif observed > 1:
        verdict = "violation"
    else:
        verdict = "error"
    return {
        "name": "one-mutation-per-effect-identity",
        "description": "The destination holds exactly one mutation for the "
                       "effect identity after the retry.",
        "expected": 1,
        "observed": observed,
        "detail": {"mutations": mutations},
        "verdict": verdict,
    }


def _oracle_event_lost(state: dict, effects: dict) -> dict:
    """Compares the ledger to destination state: the ledger must hold the
    destination's receipt for the effect and the work item must be
    completed."""
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
        "description": "The ledger records the destination's effect receipt "
                       "and the work item is completed; a stale ledger after "
                       "a dropped event is the violation.",
        "expected": {"ledger_has_receipt": True, "work_status": "completed"},
        "observed": observed,
        "detail": {"destination_receipts": receipts},
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

    oracle = spec.oracle(state, effects)
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
        "oracle": oracle,
    }
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    evidence_file = out_path / f"{drill}-{mode}.json"
    evidence_file.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    verdict = oracle["verdict"]
    if mode == "protected" and verdict == "pass":
        return 0
    if mode == "unsafe" and verdict == "violation":
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
