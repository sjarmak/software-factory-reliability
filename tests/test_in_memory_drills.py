"""Pytest coverage for the four in-memory drills in both modes.

Each drill-mode pair is run through the callable API (run_drill) for
speed; exit codes and evidence-file invariants are asserted for all eight
combinations, plus determinism and protocol-validation checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.in_memory.adapter import InMemoryAdapter  # noqa: E402
from adapters.in_memory.run_drill import DRILLS, MODES, run_drill  # noqa: E402

SCHEMA = json.loads((ROOT / "adapters" / "protocol.schema.json").read_text())

EXPECTED_EXIT = {"protected": 0, "unsafe": 2}
EXPECTED_VERDICT = {"protected": "pass", "unsafe": "violation"}
EXPECTED_BARRIER = {
    "worker-dies-agent-survives": "session-running",
    "stale-writer-completes": "before-publication",
    "effect-commits-ack-is-lost": "effect-committed-ack-pending",
    "event-is-lost": "before-ledger-event",
}
EXPECTED_FAULT = {
    "worker-dies-agent-survives": "kill_worker",
    "stale-writer-completes": "expire_lease",
    "effect-commits-ack-is-lost": "drop_event",
    "event-is-lost": "drop_event",
}
COMBOS = [(drill, mode) for drill in sorted(DRILLS) for mode in MODES]


def _run(tmp_path: Path, drill: str, mode: str) -> tuple[int, dict]:
    code = run_drill(drill, mode, out_dir=tmp_path)
    evidence_file = tmp_path / f"{drill}-{mode}.json"
    assert evidence_file.exists(), f"missing evidence file {evidence_file}"
    return code, json.loads(evidence_file.read_text())


@pytest.mark.parametrize("drill,mode", COMBOS)
def test_exit_code(tmp_path, drill, mode):
    code, _evidence = _run(tmp_path, drill, mode)
    assert code == EXPECTED_EXIT[mode]


@pytest.mark.parametrize("drill,mode", COMBOS)
def test_evidence_invariants(tmp_path, drill, mode):
    _code, ev = _run(tmp_path, drill, mode)

    assert ev["scenario"] == drill
    assert ev["mode"] == mode
    assert ev["barrier"] == EXPECTED_BARRIER[drill]
    assert ev["fault"] == {"kind": EXPECTED_FAULT[drill],
                           "at_barrier": EXPECTED_BARRIER[drill]}
    assert ev["oracle"]["verdict"] == EXPECTED_VERDICT[mode]

    # The fault barrier was reached and the run completed.
    assert EXPECTED_BARRIER[drill] in ev["barriers_reached"]
    assert ev["barriers_reached"][-1] == "run-complete"
    assert ev["campaign_coverage"]["run_complete"] is True
    assert ev["campaign_coverage"]["faults_injected"] == [ev["fault"]]

    # Per-tick event log: ticks are consecutive integers from 1.
    events = ev["events"]
    assert events, "empty event log"
    assert [e["tick"] for e in events] == list(range(1, len(events) + 1))
    kinds = [e["kind"] for e in events]
    assert "fault-injected" in kinds
    assert "barrier-reached" in kinds

    # Identity mappings are present and populated.
    identities = ev["identities"]
    for key in ("work_ids", "generations", "attempt_ids", "session_ids",
                "worker_ids", "claim_ids", "effect_ids", "artifact_ids",
                "mutation_ids", "receipt_ids", "attempt_to_session"):
        assert key in identities, f"identities missing {key}"
    assert identities["work_ids"] == ["w-1"]
    assert identities["attempt_ids"], "no attempt ids recorded"
    assert identities["session_ids"], "no session ids recorded"
    assert identities["attempt_to_session"], "no attempt-to-session mapping"


@pytest.mark.parametrize("mode", MODES)
def test_worker_dies_session_binding(tmp_path, mode):
    _code, ev = _run(tmp_path, "worker-dies-agent-survives", mode)
    bound = ev["authoritative_state"]["claims"]["w-1"]["session_ids"]
    assert len(bound) == (1 if mode == "protected" else 2)
    # The killed worker is absent from running executors while the
    # generation-1 session is still alive.
    running_workers = {w["worker_id"]
                       for w in ev["running_executors"]["workers"]}
    assert "worker-1" not in running_workers
    running_sessions = {s["session_id"]
                        for s in ev["running_executors"]["sessions"]}
    assert "sess-1" in running_sessions


@pytest.mark.parametrize("mode", MODES)
def test_stale_writer_artifact(tmp_path, mode):
    _code, ev = _run(tmp_path, "stale-writer-completes", mode)
    artifact = ev["external_effects"]["artifact"]
    expected = "artifact-g8" if mode == "protected" else "artifact-g7"
    assert artifact["artifact_id"] == expected
    assert set(ev["identities"]["artifact_ids"]) == {"artifact-g7",
                                                     "artifact-g8"}
    assert 7 in ev["identities"]["generations"]
    assert 8 in ev["identities"]["generations"]


@pytest.mark.parametrize("mode", MODES)
def test_effect_ack_mutation_count(tmp_path, mode):
    _code, ev = _run(tmp_path, "effect-commits-ack-is-lost", mode)
    mutations = [m for m in ev["external_effects"]["mutations"]
                 if m["effect_id"] == "eff-1"]
    assert len(mutations) == (1 if mode == "protected" else 2)
    kinds = [e["kind"] for e in ev["events"]]
    assert "ack-dropped" in kinds
    if mode == "protected":
        assert "effect-deduplicated" in kinds


@pytest.mark.parametrize("mode", MODES)
def test_event_lost_convergence(tmp_path, mode):
    _code, ev = _run(tmp_path, "event-is-lost", mode)
    kinds = [e["kind"] for e in ev["events"]]
    assert "ledger-update-dropped" in kinds
    work_status = ev["authoritative_state"]["work_items"]["w-1"]["status"]
    ledger = ev["authoritative_state"]["effect_ledger"]
    if mode == "protected":
        assert "reconciled" in kinds
        assert work_status == "completed"
        assert ledger["eff-1"]["status"] == "reconciled"
    else:
        assert "reconciler-absent" in kinds
        assert work_status != "completed"
        assert "eff-1" not in ledger


@pytest.mark.parametrize("drill,mode", COMBOS)
def test_determinism(tmp_path, drill, mode):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    code_a = run_drill(drill, mode, out_dir=dir_a)
    code_b = run_drill(drill, mode, out_dir=dir_b)
    assert code_a == code_b
    text_a = (dir_a / f"{drill}-{mode}.json").read_text()
    text_b = (dir_b / f"{drill}-{mode}.json").read_text()
    assert text_a == text_b


def test_unknown_drill_and_mode_exit_1(tmp_path, capsys):
    assert run_drill("no-such-drill", "protected", out_dir=tmp_path) == 1
    assert run_drill("event-is-lost", "no-such-mode", out_dir=tmp_path) == 1
    captured = capsys.readouterr()
    assert "unknown drill" in captured.err
    assert "unknown mode" in captured.err


def test_adapter_rejects_invalid_commands():
    adapter = InMemoryAdapter()
    bogus = adapter.handle({"op": "bogus"})
    assert bogus["ok"] is False
    bad_fault = adapter.handle({"op": "inject_fault",
                                "params": {"kind": "nuke",
                                           "at_barrier": "x"}})
    assert bad_fault["ok"] is False
    unseeded = adapter.handle({"op": "start"})
    assert unseeded["ok"] is False
    assert "seed" in unseeded["error"]


def test_observations_match_schema():
    adapter = InMemoryAdapter()
    commands = [
        {"op": "seed", "params": {"scenario": "event-is-lost",
                                  "mode": "protected"}},
        {"op": "start"},
        {"op": "wait_for_barrier", "params": {"name": "before-ledger-event"}},
        {"op": "inject_fault", "params": {"kind": "drop_event",
                                          "at_barrier":
                                          "before-ledger-event"}},
        {"op": "wait_for_barrier", "params": {"name": "run-complete"}},
        {"op": "read_authoritative_state"},
        {"op": "read_external_effects"},
        {"op": "read_running_executors"},
        {"op": "read_campaign_coverage"},
        {"op": "collect_evidence"},
    ]
    for command in commands:
        jsonschema.validate(command, SCHEMA)
        observation = adapter.handle(command)
        assert observation["ok"] is True, observation
        jsonschema.validate(observation, SCHEMA)


# Regression tests for the 2026-08-09 review findings.

def test_fenced_publish_rejects_stale_generation_without_prior_artifact():
    """The fence compares against current ownership, not artifact history;
    the original implementation accepted any generation when no artifact
    existed yet."""
    from adapters.in_memory.model import Factory
    f = Factory("dedup", "fenced", True)
    f.add_work("w-1", generation=7)
    f.start_worker("worker-1")
    f.claim_work("w-1", attempt=1)
    f.launch_session("w-1", "worker-1", attempt=1)
    f.advance_generation("w-1")
    assert f.publish("w-1", "artifact-g7", 7, "sess-1") is False
    assert f.destination.artifact is None
    assert f.publish("w-1", "artifact-g8", 8, "sess-1") is True


def test_fenced_completion_rejects_stale_generation():
    from adapters.in_memory.model import Factory
    f = Factory("dedup", "fenced", True)
    f.add_work("w-1", generation=7)
    f.start_worker("worker-1")
    f.claim_work("w-1", attempt=1)
    f.launch_session("w-1", "worker-1", attempt=1)
    f.advance_generation("w-1")
    assert f.complete_work("w-1", "sess-1", attempt=1, generation=7) is False
    assert f.work_items["w-1"].status != "completed"
    assert any(e["kind"] == "completion-rejected-stale" for e in f.events)
    assert f.complete_work("w-1", "sess-1", attempt=1, generation=8) is True


def test_oracles_error_on_vacuous_evidence():
    """An oracle must not pass on a final state that never saw the fault or
    the dangerous operation; the original oracles did."""
    from adapters.in_memory.run_drill import (
        _oracle_worker_dies, _oracle_stale_writer, _oracle_effect_ack,
        _oracle_event_lost)
    vacuous = {
        "events": [],
        "authoritative_state": {
            "claims": {"w-1": {"session_ids": ["sess-1"]}},
            "effect_ledger": {},
            "work_items": {"w-1": {"status": "completed"}},
        },
        "external_effects": {"mutations": [],
                             "artifact": {"artifact_id": "artifact-g8"}},
    }
    for oracle in (_oracle_worker_dies, _oracle_stale_writer,
                   _oracle_effect_ack, _oracle_event_lost):
        verdict = oracle(vacuous)["verdict"]
        assert verdict == "error", f"{oracle.__name__} returned {verdict}"


@pytest.mark.parametrize("mode,expected_kind", [
    ("protected", "completion-rejected-stale"),
    ("unsafe", "outcome-accepted"),
])
def test_stale_writer_drill_exercises_stale_completion(tmp_path, mode,
                                                       expected_kind):
    """The drill drives a generation-7 completion attempt after takeover;
    the fenced arm rejects it and the unsafe arm accepts it."""
    from adapters.in_memory.run_drill import run_drill
    run_drill("stale-writer-completes", mode, tmp_path)
    evidence = json.loads(
        (tmp_path / f"stale-writer-completes-{mode}.json").read_text())
    stale_completions = [e for e in evidence["events"]
                         if e.get("kind") == expected_kind
                         and e.get("generation") == 7]
    assert stale_completions, (
        f"no {expected_kind} event for generation 7 in {mode} evidence")
