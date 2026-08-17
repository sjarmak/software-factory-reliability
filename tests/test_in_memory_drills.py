"""Pytest coverage for the seven in-memory drills in both modes.

Each drill-mode pair is run through the callable API (run_drill) for
speed; exit codes and evidence-file invariants are asserted for all
fourteen combinations, plus determinism and protocol-validation checks.
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
    "child-completes-after-join": "before-join",
    "request-accepted-effect-never-applied": "request-accepted",
    "source-advances-view-answers-anyway": "view-current",
}
# Full fault records: a targeted fault carries the target it was aimed at, so
# the evidence names which lease expired rather than leaving it to the log.
EXPECTED_FAULT = {
    "worker-dies-agent-survives": {"kind": "kill_worker"},
    "stale-writer-completes": {"kind": "expire_lease"},
    "effect-commits-ack-is-lost": {"kind": "drop_event"},
    "event-is-lost": {"kind": "drop_event"},
    "child-completes-after-join": {"kind": "expire_lease", "target": "c-3"},
    "request-accepted-effect-never-applied": {"kind": "drop_event"},
    "source-advances-view-answers-anyway": {"kind": "drop_event"},
}
# Work items each scenario seeds, in identity_snapshot order (sorted).
EXPECTED_WORK_IDS = {
    "worker-dies-agent-survives": ["w-1"],
    "stale-writer-completes": ["w-1"],
    "effect-commits-ack-is-lost": ["w-1"],
    "event-is-lost": ["w-1"],
    "child-completes-after-join": ["c-1", "c-2", "c-3", "w-1"],
    "request-accepted-effect-never-applied": ["w-1"],
    "source-advances-view-answers-anyway": ["w-1"],
}
COMBOS = [(drill, mode) for drill in sorted(DRILLS) for mode in MODES]


def _expected_fault(drill: str) -> dict:
    record = dict(EXPECTED_FAULT[drill])
    record["at_barrier"] = EXPECTED_BARRIER[drill]
    return record


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
    assert ev["fault"] == _expected_fault(drill)
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
    assert identities["work_ids"] == EXPECTED_WORK_IDS[drill]
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


@pytest.mark.parametrize("mode,expected_artifact,expected_folded", [
    ("protected", "merge-c-1+c-2+c-3", ["c-1", "c-2", "c-3"]),
    ("unsafe", "merge-c-3", ["c-3"]),
])
def test_join_folds_published_children(tmp_path, mode, expected_artifact,
                                       expected_folded):
    """The merged artifact's identity names the children it folded. The
    protected run recomputes the fold after the straggler publishes; the
    unsafe run ends holding the straggler's own partial merge."""
    _code, ev = _run(tmp_path, "child-completes-after-join", mode)
    assert ev["external_effects"]["artifact"]["artifact_id"] == expected_artifact
    assert ev["oracle"]["observed"]["folded_children"] == expected_folded
    assert ev["oracle"]["expected"]["folded_children"] == ["c-1", "c-2", "c-3"]
    kinds = [e["kind"] for e in ev["events"]]
    assert ("join-recomputed" in kinds) == (mode == "protected")
    assert ("join-recompute-absent" in kinds) == (mode == "unsafe")


@pytest.mark.parametrize("mode,expected_kind", [
    ("protected", "publish-rejected-stale"),
    ("unsafe", "publish-unfenced-applied"),
])
def test_join_fence_decides_the_late_merge_write(tmp_path, mode, expected_kind):
    """The straggler's write into the merge slot carries join generation 1
    after ownership advanced to 2. The fence is the only thing that stops it,
    and the fold recomputation is a separate protection: this asserts the
    fence half on its own."""
    _code, ev = _run(tmp_path, "child-completes-after-join", mode)
    late = [e for e in ev["events"]
            if e.get("kind") == expected_kind
            and e.get("work_id") == "w-1"
            and e.get("generation") == 1]
    assert late, f"no {expected_kind} for the generation-1 merge write"
    # The straggler's own work item completes in both arms; only its write to
    # the parent's merge slot is fenced.
    assert ev["authoritative_state"]["work_items"]["c-3"]["status"] == "completed"


def test_join_refuses_to_fire_over_an_undispositioned_child():
    """Control run with no fault injected: the straggler still holds its lease
    and has published nothing, so it is undispositioned and the join must
    report incomplete rather than fold around it."""
    adapter = InMemoryAdapter()
    for command in (
        {"op": "seed", "params": {"scenario": "child-completes-after-join",
                                  "mode": "protected"}},
        {"op": "start"},
        {"op": "wait_for_barrier", "params": {"name": "before-join"}},
        {"op": "wait_for_barrier", "params": {"name": "run-complete"}},
    ):
        assert adapter.handle(command)["ok"] is True, command
    events = adapter.handle({"op": "collect_evidence"})["data"]["events"]
    incomplete = [e for e in events if e["kind"] == "join-incomplete"]
    assert incomplete, "the join folded a set containing an undispositioned child"
    assert incomplete[0]["undispositioned"] == ["c-3"]
    assert "join-published" not in [e["kind"] for e in events]


def test_dispositions_are_derived_from_child_evidence():
    """published needs both the completion record and the destination
    mutation; a child with neither and no lease is blocked; a child still
    holding its lease with nothing published is undispositioned."""
    from adapters.in_memory.model import Factory
    f = Factory("dedup", "fenced", True)
    for child in ("c-1", "c-2", "c-3"):
        f.add_work(child, generation=1)
        f.claim_work(child, attempt=1)
    f.start_worker("worker-1")
    f.launch_session("c-1", "worker-1", attempt=1)
    f.apply_effect("c-1", "sess-1", "eff-c-1", "payload", attempt=1)
    f.complete_work("c-1", "sess-1", attempt=1, generation=1)
    f.expire_lease("c-2")
    assert f.derive_dispositions(["c-1", "c-2", "c-3"]) == {
        "c-1": "published", "c-2": "blocked", "c-3": "undispositioned"}
    # A completion with no mutation at the destination is a self-report, and
    # must not count as published.
    f.complete_work("c-3", "sess-1", attempt=1, generation=1)
    assert f.derive_dispositions(["c-3"]) == {"c-3": "undispositioned"}


@pytest.mark.parametrize("mode,expected_outcome,expected_basis", [
    ("protected", "requested-and-queued", "destination-readback"),
    ("unsafe", "requested-and-applied", "own-dispatch-result"),
])
def test_request_outcome_reports_the_destination(tmp_path, mode,
                                                 expected_outcome,
                                                 expected_basis):
    """The destination is empty in both arms; only the protected boundary
    says so. The basis field records which source the outcome came from, so
    a correct-looking outcome computed the wrong way is still visible."""
    _code, ev = _run(tmp_path, "request-accepted-effect-never-applied", mode)
    assert ev["external_effects"]["mutations"] == []
    kinds = [e["kind"] for e in ev["events"]]
    assert "apply-effect-dropped" in kinds
    assert "effect-applied" not in kinds
    classified = [e for e in ev["events"]
                  if e["kind"] == "outcome-classified"]
    assert len(classified) == 1
    assert classified[0]["basis"] == expected_basis
    request = ev["authoritative_state"]["requests"]["req-1"]
    assert request["outcome"] == expected_outcome
    assert request["requested_effects"] == ["eff-1"]


@pytest.mark.parametrize("mode,expected_terminal", [
    ("protected", {"state": "expired",
                   "reason": "accepted request still unapplied when the "
                             "window closed"}),
    ("unsafe", None),
])
def test_accepted_request_holds_a_terminal_record(tmp_path, mode,
                                                  expected_terminal):
    """A returned outcome is a value the caller may discard. The terminal
    record is the durable half, and the unsafe arm has none: this asserts it
    separately from the outcome's truthfulness so neither check can carry
    the other."""
    _code, ev = _run(tmp_path, "request-accepted-effect-never-applied", mode)
    request = ev["authoritative_state"]["requests"]["req-1"]
    assert request["terminal"] == expected_terminal
    kinds = [e["kind"] for e in ev["events"]]
    assert ("request-terminal" in kinds) == (mode == "protected")
    assert ("request-sweep-absent" in kinds) == (mode == "unsafe")
    assert ev["oracle"]["detail"]["recorded"] is (mode == "protected")


def test_classify_by_readback_names_each_outcome():
    """All six outcomes are reachable from the same classifier. The drill
    drives two of them; the rest are pinned here, including the documented
    call on a reported failure that left nothing at the destination."""
    from adapters.in_memory.model import Factory
    f = Factory("dedup", "fenced", True)
    f.add_work("w-1", generation=1)
    f.start_worker("worker-1")
    f.claim_work("w-1", attempt=1)
    f.launch_session("w-1", "worker-1", attempt=1)

    f.reject_request("req-reject", "w-1", ["eff-a"], "unknown recipe")
    assert f.classify_by_readback("req-reject") == "rejected-before-mutation"

    f.accept_request("req-queued", "w-1", ["eff-a"])
    assert f.classify_by_readback("req-queued") == "requested-and-queued"

    f.apply_effect("w-1", "sess-1", "eff-a", "payload-a", attempt=1)
    assert f.classify_by_readback("req-queued") == "requested-and-applied"

    f.accept_request("req-partial", "w-1", ["eff-a", "eff-b"])
    assert f.classify_by_readback("req-partial") == "partially-applied"
    # The same split set, once the application leg reports an error: the
    # caller may no longer retry the whole request unchanged.
    f.requests["req-partial"].failed = True
    assert f.classify_by_readback("req-partial") == "failed-after-mutation"

    # A reported failure with nothing at the destination is still an
    # accepted request owing a terminal record, not a failed mutation.
    f.accept_request("req-clean-failure", "w-1", ["eff-c"])
    f.requests["req-clean-failure"].failed = True
    assert f.classify_by_readback("req-clean-failure") == "requested-and-queued"

    f.accept_request("req-blind", "w-1", ["eff-d"])
    f.requests["req-blind"].observation_ok = False
    assert (f.classify_by_readback("req-blind")
            == "unknown-because-observation-failed")


def test_dispatch_classification_ignores_the_destination():
    """The unsafe path's defect in isolation: the same empty destination
    that makes the readback say queued makes the dispatch path say applied,
    because it never looks."""
    from adapters.in_memory.model import Factory
    f = Factory("dedup", "fenced", True)
    f.add_work("w-1", generation=1)
    f.accept_request("req-1", "w-1", ["eff-1"])
    assert f.destination.mutations == []
    assert f.classify_by_dispatch("req-1") == "requested-and-applied"
    assert f.classify_by_readback("req-1") == "requested-and-queued"


@pytest.mark.parametrize("mode,expected_outcome,expected_basis", [
    ("protected", "lag-exceeded", "published-position-compared-to-source"),
    ("unsafe", "results-complete", "index-contents-only"),
])
def test_stale_view_answers_within_its_lag(tmp_path, mode, expected_outcome,
                                           expected_basis):
    """One record reached the destination and never reached the view. The
    unsafe arm returns a complete result set holding none of it, which is a
    false absence; the protected arm names the gap instead of answering."""
    _code, ev = _run(tmp_path, "source-advances-view-answers-anyway", mode)
    kinds = [e["kind"] for e in ev["events"]]
    assert "index-update-dropped" in kinds
    assert len([e for e in ev["events"] if e["kind"] == "view-indexed"]) == 1

    view = ev["authoritative_state"]["views"]["view-1"]
    assert view["consumed_position"] == 1
    assert view["source_position"] == 2
    assert view["entries"] == ["eff-0"]
    assert "eff-1" in {m["effect_id"]
                       for m in ev["external_effects"]["mutations"]}

    query = [e for e in ev["events"] if e["kind"] == "view-queried"]
    assert len(query) == 1
    assert query[0]["outcome"] == expected_outcome
    assert query[0]["basis"] == expected_basis
    # The unsafe arm's answer is an empty list, not a refusal: a caller
    # cannot tell it apart from the record not existing.
    assert query[0]["matches"] == ([] if mode == "unsafe" else None)


@pytest.mark.parametrize("mode,expected_state,expected_lag", [
    ("protected", "stale", 1),
    ("unsafe", "fresh", None),
])
def test_stale_view_health_surface(tmp_path, mode, expected_state,
                                   expected_lag):
    """The health surface is asserted apart from the query outcome, so
    neither check can carry the other. Same view, same tick, two verdicts:
    the liveness surface reports fresh because the indexer ran."""
    _code, ev = _run(tmp_path, "source-advances-view-answers-anyway", mode)
    health = [e for e in ev["events"] if e["kind"] == "view-health-reported"]
    assert len(health) == 1
    assert health[0]["state"] == expected_state
    assert health[0]["lag"] == expected_lag
    assert ev["oracle"]["detail"]["health_agrees"] is (mode == "protected")
    assert ev["oracle"]["detail"]["answer_truthful"] is (mode == "protected")


def test_view_query_reaches_every_outcome():
    """All three query outcomes come from the same call. A view that has
    never published a position is lag-unknown, which is a third state and
    not a slow version of fresh."""
    from adapters.in_memory.model import Factory, VIEW_QUERY_OUTCOMES
    f = Factory("dedup", "fenced", True)
    f.add_work("w-1", generation=1)
    f.start_worker("worker-1")
    f.claim_work("w-1", attempt=1)
    f.launch_session("w-1", "worker-1", attempt=1)
    f.add_view("view-1", max_lag=0)

    assert f.query_view_lag_checked("view-1", "eff-a")["outcome"] == (
        "lag-unknown")

    f.apply_effect("w-1", "sess-1", "eff-a", "payload-a", attempt=1)
    f.index_view_entry("view-1", "eff-a")
    answered = f.query_view_lag_checked("view-1", "eff-a")
    assert answered["outcome"] == "results-complete"
    assert answered["matches"] == ["eff-a"]

    f.apply_effect("w-1", "sess-1", "eff-b", "payload-b", attempt=1)
    behind = f.query_view_lag_checked("view-1", "eff-b")
    assert behind["outcome"] == "lag-exceeded"
    assert behind["lag"] == 1 and behind["required_lag"] == 0
    # A caller willing to read one position behind gets an answer, and the
    # answer is still wrong about eff-b: the contract bounds the staleness
    # it accepts, it does not make the missing record appear.
    tolerant = f.query_view_lag_checked("view-1", "eff-b", max_lag=1)
    assert tolerant["outcome"] == "results-complete"
    assert tolerant["matches"] == []

    outcomes = {e["outcome"] for e in f.events if e["kind"] == "view-queried"}
    assert outcomes == set(VIEW_QUERY_OUTCOMES)


def test_liveness_health_cannot_report_a_lag():
    """The unsafe health surface's defect in isolation: it calls a view
    fresh from the fact that its indexer started, on a state where the
    position comparison already says stale."""
    from adapters.in_memory.model import Factory
    f = Factory("dedup", "fenced", True)
    f.add_work("w-1", generation=1)
    f.start_worker("worker-1")
    f.claim_work("w-1", attempt=1)
    f.launch_session("w-1", "worker-1", attempt=1)
    f.add_view("view-1", max_lag=0)
    f.apply_effect("w-1", "sess-1", "eff-a", "payload-a", attempt=1)
    f.index_view_entry("view-1", "eff-a")
    f.apply_effect("w-1", "sess-1", "eff-b", "payload-b", attempt=1)

    by_position = f.view_health_by_position("view-1")
    by_liveness = f.view_health_by_liveness("view-1")
    assert by_position["state"] == "stale" and by_position["lag"] == 1
    assert by_liveness["state"] == "fresh" and by_liveness["lag"] is None
    assert by_position["basis"] != by_liveness["basis"]


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
        _oracle_event_lost, _oracle_child_after_join,
        _oracle_request_never_applied, _oracle_stale_view)
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
                   _oracle_effect_ack, _oracle_event_lost,
                   _oracle_child_after_join, _oracle_request_never_applied,
                   _oracle_stale_view):
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
