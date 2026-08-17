"""Run one reliability drill against the in-memory reference factory.

Usage:
    python3 src/adapters/in_memory/run_drill.py <drill>
        --mode protected|unsafe [--out-dir out/evidence]

Drills: worker-dies-agent-survives, stale-writer-completes,
effect-commits-ack-is-lost, event-is-lost, child-completes-after-join,
request-accepted-effect-never-applied,
source-advances-view-answers-anyway, state-changes-check-does-not,
guard-refuses-repair-never-runs.

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

# Running this file as a script puts its own directory on sys.path rather than
# src/, so the absolute import below raised ModuleNotFoundError: No module
# named 'adapters'. Put src/ on the path when it is missing, so the documented
# invocation needs no PYTHONPATH and no install step.
if __package__ in (None, ""):  # pragma: no cover - script-invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters.in_memory.adapter import (CHECK_ID, CHILD_IDS, EFFECT_ID,
                                        PRIOR_EFFECT_ID, REQUEST_ID,
                                        RESOURCE_ID, STRAGGLER_ID, VIEW_ID,
                                        WORK_ID, InMemoryAdapter)


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


def _oracle_child_after_join(evidence: dict) -> dict:
    """The merged artifact at the destination folds exactly the children whose
    own evidence says published. The verdict only counts if the log shows the
    written-off child returning after the join fired and writing into the merge
    slot; a correct-looking merge with no straggler on record proves nothing.

    Published is recomputed here from the child's own evidence (its completion
    record plus its mutation at the destination) rather than read back from the
    factory's disposition log, so the oracle does not inherit the belief it is
    checking."""
    events = evidence["events"]
    state = evidence["authoritative_state"]
    effects = evidence["external_effects"]
    description = ("The straggler was written off, the join published a merge, "
                   "the straggler then completed and wrote into the merge slot, "
                   "and the merged artifact folds exactly the children whose "
                   "own evidence says published.")
    missing = []
    writeoff_tick = _first_tick(events, "lease-expired", work_id=STRAGGLER_ID)
    if writeoff_tick is None:
        missing.append(f"the injected write-off of {STRAGGLER_ID}")
    join_tick = _first_tick(events, "join-published")
    if join_tick is None or (writeoff_tick is not None
                             and join_tick <= writeoff_tick):
        missing.append("a join publishing after the write-off")
    straggler_done_tick = _first_tick(events, "outcome-accepted",
                                      work_id=STRAGGLER_ID)
    if straggler_done_tick is None or (join_tick is not None
                                       and straggler_done_tick <= join_tick):
        missing.append("the straggler completing after the join")
    late_write_kind = None
    late_write_tick = None
    for kind in ("publish-rejected-stale", "publish-unfenced-applied",
                 "publish-unfenced-skipped", "publish-accepted"):
        tick = _first_tick(events, kind, work_id=WORK_ID, generation=1)
        if tick is not None and join_tick is not None and tick > join_tick:
            late_write_kind = kind
            late_write_tick = tick
            break
    if late_write_tick is None:
        missing.append("the straggler writing into the merge slot after the join")
    artifact = effects["artifact"]
    if artifact is None:
        missing.append("a merged artifact at the destination")
    if missing:
        return _preconditions_failed(missing, "join-folds-published-children",
                                     description)
    applied = {m["effect_id"] for m in effects["mutations"]}
    published = sorted(
        child for child in CHILD_IDS
        if state["work_items"].get(child, {}).get("status") == "completed"
        and f"eff-{child}" in applied)
    folded = None
    for event in events:
        if (event.get("kind") == "merge-prepared"
                and event.get("artifact_id") == artifact["artifact_id"]):
            folded = event.get("folded_children")
    verdict = "error" if folded is None else (
        "pass" if folded == published else "violation")
    return {
        "name": "join-folds-published-children",
        "description": description,
        "expected": {"folded_children": published},
        "observed": {"folded_children": folded,
                     "artifact": artifact["artifact_id"],
                     "late_write": late_write_kind},
        "detail": {"writeoff_tick": writeoff_tick, "join_tick": join_tick,
                   "straggler_completion_tick": straggler_done_tick,
                   "late_write_tick": late_write_tick},
        "verdict": verdict,
    }


def _oracle_request_never_applied(evidence: dict) -> dict:
    """Two checks over one accepted request whose effect never landed.

    Truthfulness: the outcome the boundary returned must not be
    success-shaped while the destination holds none of the requested
    effects. Terminality: the accepted request must hold a durable terminal
    record at end of run, since a returned value is not a record and a
    caller that discarded it leaves nothing for a later scan to find.

    Landed effects are recounted here from the destination snapshot rather
    than read from the factory's own classification, so the oracle does not
    inherit the belief it is checking. The verdict only counts if the log
    shows the application leg actually dropped after acceptance and the
    boundary actually classified afterwards.
    """
    events = evidence["events"]
    state = evidence["authoritative_state"]
    effects = evidence["external_effects"]
    description = ("The request was accepted, its application leg was "
                   "dropped, the boundary classified the outcome, and the "
                   "window closed: the returned outcome is not success-shaped "
                   "over an empty destination and the request holds a "
                   "terminal record.")
    missing = []
    accept_tick = _first_tick(events, "request-accepted", request_id=REQUEST_ID)
    if accept_tick is None:
        missing.append("the accepted request")
    drop_tick = _first_tick(events, "apply-effect-dropped",
                            request_id=REQUEST_ID)
    if drop_tick is None or (accept_tick is not None
                             and drop_tick <= accept_tick):
        missing.append("the dropped application leg after acceptance")
    classify_tick = _first_tick(events, "outcome-classified",
                                request_id=REQUEST_ID)
    if classify_tick is None or (drop_tick is not None
                                 and classify_tick <= drop_tick):
        missing.append("an outcome classified after the drop")
    close_tick = None
    for kind in ("request-terminal", "request-sweep-absent"):
        tick = _first_tick(events, kind, request_id=REQUEST_ID)
        if tick is not None:
            close_tick = tick
            break
    if close_tick is None or (classify_tick is not None
                              and close_tick <= classify_tick):
        missing.append("the window closing after the classification")
    if missing:
        return _preconditions_failed(
            missing, "outcome-matches-postcondition", description)
    request = state.get("requests", {}).get(REQUEST_ID, {})
    requested = request.get("requested_effects", [])
    applied = {m["effect_id"] for m in effects["mutations"]}
    landed = sorted(effect for effect in requested if effect in applied)
    outcome = request.get("outcome")
    terminal = request.get("terminal")
    truthful = not (outcome == "requested-and-applied" and not landed)
    recorded = terminal is not None
    if outcome is None:
        verdict = "error"
    else:
        verdict = "pass" if (truthful and recorded) else "violation"
    return {
        "name": "outcome-matches-postcondition",
        "description": description,
        "expected": {"success_shaped_over_empty_destination": False,
                     "terminal_record": True},
        "observed": {"reported_outcome": outcome,
                     "landed_effects": landed,
                     "requested_effects": requested,
                     "terminal_record": terminal},
        "detail": {"accept_tick": accept_tick, "drop_tick": drop_tick,
                   "classify_tick": classify_tick, "close_tick": close_tick,
                   "truthful": truthful, "recorded": recorded},
        "verdict": verdict,
    }


def _oracle_stale_view(evidence: dict) -> dict:
    """Two checks over one derived view that fell behind its source.

    The answer: a query outcome that claims a complete result set must not
    be returned when the destination holds the queried record and the view
    does not. The surface: a health verdict of fresh must not be reported
    when the view's published position is further behind the source than
    its declared contract allows.

    Both are recomputed here from the destination snapshot, the view's
    entry list, and the view's published position, so the oracle does not
    inherit either surface's belief. They are independent by construction:
    the first never reads a position and the second never reads an entry,
    so neither can carry the other. The verdict only counts if the log
    shows the view current at the barrier, the source advancing, the
    index-update actually dropped, and both surfaces answering afterwards.
    """
    events = evidence["events"]
    state = evidence["authoritative_state"]
    effects = evidence["external_effects"]
    description = ("The view consumed the source's first record, the source "
                   "advanced, the index-update was dropped, and both the "
                   "query and the health surface answered afterwards: "
                   "neither claims a complete result nor a fresh view while "
                   "the view is behind the source.")
    missing = []
    indexed_tick = _first_tick(events, "view-indexed", effect_id="eff-0")
    if indexed_tick is None:
        missing.append("the view consuming the source's first record")
    fault_tick = _first_tick(events, "fault-injected")
    write_tick = _first_tick(events, "effect-applied", effect_id=EFFECT_ID)
    if write_tick is None or (fault_tick is not None
                              and write_tick <= fault_tick):
        missing.append("a source write after the fault injection")
    drop_tick = _first_tick(events, "index-update-dropped",
                            effect_id=EFFECT_ID)
    if drop_tick is None or (write_tick is not None
                             and drop_tick <= write_tick):
        missing.append("the dropped index-update for that write")
    query_tick = _first_tick(events, "view-queried", term=EFFECT_ID)
    if query_tick is None or (drop_tick is not None
                              and query_tick <= drop_tick):
        missing.append("a query for that record after the drop")
    health_tick = _first_tick(events, "view-health-reported", view_id=VIEW_ID)
    if health_tick is None or (query_tick is not None
                               and health_tick <= query_tick):
        missing.append("a health report after the query")
    if missing:
        return _preconditions_failed(missing, "view-answers-within-its-lag",
                                     description)

    view = state.get("views", {}).get(VIEW_ID, {})
    entries = view.get("entries", [])
    at_source = EFFECT_ID in {m["effect_id"] for m in effects["mutations"]}
    in_view = EFFECT_ID in entries
    query = [e for e in events
             if e.get("kind") == "view-queried" and e.get("term") == EFFECT_ID]
    query_outcome = query[-1].get("outcome") if query else None
    # The answer rail reads entries against the destination and never a
    # position, so a view that advanced its position without indexing the
    # record still fails it.
    answer_truthful = not (query_outcome == "results-complete"
                           and at_source and not in_view)

    consumed = view.get("consumed_position")
    source_position = len(effects["mutations"])
    lag = None if consumed is None else source_position - consumed
    max_lag = view.get("max_lag")
    health = [e for e in events
              if e.get("kind") == "view-health-reported"
              and e.get("view_id") == VIEW_ID]
    health_state = health[-1].get("state") if health else None
    health_basis = health[-1].get("basis") if health else None
    behind = lag is None or max_lag is None or lag > max_lag
    health_agrees = not (health_state == "fresh" and behind)

    if query_outcome is None or health_state is None:
        verdict = "error"
    else:
        verdict = ("pass" if (answer_truthful and health_agrees)
                   else "violation")
    return {
        "name": "view-answers-within-its-lag",
        "description": description,
        "expected": {"complete_result_over_a_record_the_view_lacks": False,
                     "fresh_health_while_behind_contract": False},
        "observed": {"query_outcome": query_outcome,
                     "record_at_source": at_source,
                     "record_in_view": in_view,
                     "health_state": health_state,
                     "health_basis": health_basis,
                     "consumed_position": consumed,
                     "source_position": source_position,
                     "lag": lag, "max_lag": max_lag},
        "detail": {"indexed_tick": indexed_tick, "write_tick": write_tick,
                   "drop_tick": drop_tick, "query_tick": query_tick,
                   "health_tick": health_tick,
                   "answer_truthful": answer_truthful,
                   "health_agrees": health_agrees},
        "verdict": verdict,
    }


def _oracle_check_falsifiability(evidence: dict) -> dict:
    """Two checks over one of the factory's own checks.

    Discrimination: a check evaluated on both sides of a state change that
    crosses its claim must not return the same verdict twice. A check that
    cannot go red and a check that cannot go green both fail this rail, and
    they fail it identically, because each returns a constant verdict
    across a transition it says it distinguishes.

    Provenance: the value a check reads at evaluation time must not be a
    value that check wrote. Self-supplied input makes the verdict a
    property of the writer, and the branch reporting the other verdict
    unreachable.

    Both are recomputed here from the ordered event log and the
    destination's mutation list, never from the check's own report of
    whether it is working. They share no input: the discrimination rail
    reads verdicts and mutations and never a key, the provenance rail reads
    keys and never a verdict. The verdict only counts if the log shows the
    check registered, its write dropped, an evaluation while the record was
    missing, the record then applied, and a second evaluation after that.
    """
    events = evidence["events"]
    effects = evidence["external_effects"]
    description = ("A check was registered over one destination record, the "
                   "write was dropped, the check ran, the record was then "
                   "applied, and the check ran again: its verdict changed "
                   "with the state and its input was not its own output.")
    missing = []
    registered_tick = _first_tick(events, "check-registered",
                                  check_id=CHECK_ID)
    if registered_tick is None:
        missing.append("the check being registered")
    fault_tick = _first_tick(events, "fault-injected")
    drop_tick = _first_tick(events, "effect-application-dropped",
                            effect_id=EFFECT_ID)
    if drop_tick is None or (fault_tick is not None
                             and drop_tick <= fault_tick):
        missing.append("the dropped application of that record")
    evaluations = [e for e in events
                   if e.get("kind") == "check-evaluated"
                   and e.get("check_id") == CHECK_ID]
    before = next((e for e in evaluations
                   if drop_tick is not None and e["tick"] > drop_tick), None)
    if before is None:
        missing.append("an evaluation while the record was missing")
    applied_tick = _first_tick(events, "effect-applied", effect_id=EFFECT_ID)
    if applied_tick is None or (before is not None
                                and applied_tick <= before["tick"]):
        missing.append("the record being applied after that evaluation")
    after = next((e for e in reversed(evaluations)
                  if applied_tick is not None and e["tick"] > applied_tick),
                 None)
    if after is None:
        missing.append("a second evaluation after the record was applied")
    if missing:
        return _preconditions_failed(missing, "check-discriminates-and-reads-"
                                     "what-it-did-not-write", description)

    at_source = EFFECT_ID in {m["effect_id"] for m in effects["mutations"]}
    verdict_before = before.get("verdict")
    verdict_after = after.get("verdict")
    # The state genuinely crossed the claim between the two evaluations:
    # the record was missing at the first (its write was dropped) and is
    # present at the destination now. A check that reports the state has to
    # report those two differently.
    discriminates = at_source and verdict_before != verdict_after

    written_keys = {e.get("key") for e in events
                    if e.get("kind") == "check-key-written"
                    and e.get("check_id") == CHECK_ID}
    self_supplied = sorted(
        {e.get("read_key") for e in evaluations} & written_keys)
    independent_input = not self_supplied

    if verdict_before is None or verdict_after is None:
        oracle_verdict = "error"
    else:
        oracle_verdict = ("pass" if (discriminates and independent_input)
                          else "violation")
    return {
        "name": "check-discriminates-and-reads-what-it-did-not-write",
        "description": description,
        "expected": {"same_verdict_across_the_state_change": False,
                     "input_written_by_the_check_itself": False},
        "observed": {"verdict_before_repair": verdict_before,
                     "verdict_after_repair": verdict_after,
                     "basis": after.get("basis"),
                     "read_key": after.get("read_key"),
                     "record_at_source": at_source,
                     "keys_written_by_the_check": sorted(written_keys),
                     "self_supplied_inputs": self_supplied},
        "detail": {"registered_tick": registered_tick, "drop_tick": drop_tick,
                   "before_tick": before["tick"],
                   "applied_tick": applied_tick, "after_tick": after["tick"],
                   "discriminates": discriminates,
                   "independent_input": independent_input},
        "verdict": oracle_verdict,
    }


def _oracle_guard_before_repair(evidence: dict) -> dict:
    """Two checks over one guard and the repair that satisfies it.

    Progress: an operation whose precondition the factory itself can restore
    must still reach the destination after the resource drifts. Repair
    reachability: the step that restores the precondition must actually run
    after the drift, and the resource must conform at end of run.

    Both are recomputed here from ground truth and share no input. The
    progress rail reads the destination's mutation list and never the
    resource or a refusal; the repair rail reads the resource's final state
    and the repair events and never the destination. Neither can carry the
    other, and the second exists because passing the first is not
    sufficient: a write that lands while the resource stays drifted has
    deleted the guard rather than ordered it, and loses the anomaly report
    with it.

    The verdict only counts if the log shows a write landing while the
    resource conformed, the drift after that, and two further attempts at
    the same write. The second attempt is what makes permanence visible: a
    failure that repeats identically is a different finding from a failure
    that happened once.
    """
    events = evidence["events"]
    state = evidence["authoritative_state"]
    effects = evidence["external_effects"]
    description = ("A guarded write landed while the resource conformed, the "
                   "resource then drifted out of the state that write "
                   "requires, and two further attempts ran: the write reached "
                   "the destination and the repair that restores the "
                   "precondition ran after the drift.")
    missing = []
    prior_tick = _first_tick(events, "guarded-write-applied",
                             effect_id=PRIOR_EFFECT_ID)
    if prior_tick is None:
        missing.append("a guarded write landing while the resource conformed")
    fault_tick = _first_tick(events, "fault-injected")
    drift_tick = _first_tick(events, "resource-drifted",
                             resource_id=RESOURCE_ID)
    if drift_tick is None or (fault_tick is not None
                              and drift_tick <= fault_tick):
        missing.append("the injected drift of the resource")
    if (drift_tick is not None and prior_tick is not None
            and drift_tick <= prior_tick):
        missing.append("the drift landing after the conforming write")
    attempts = [e for e in events
                if e.get("kind") in ("guarded-write-applied",
                                     "guarded-write-refused")
                and e.get("effect_id") == EFFECT_ID
                and drift_tick is not None and e["tick"] > drift_tick]
    if len(attempts) < 2:
        missing.append("two attempts at the guarded write after the drift")
    resource = state.get("resources", {}).get(RESOURCE_ID)
    if resource is None:
        missing.append("the resource in the final state snapshot")
    if missing:
        return _preconditions_failed(missing, "precondition-is-repairable",
                                     description)

    # Progress rail: recounted from the destination alone.
    at_destination = EFFECT_ID in {m["effect_id"] for m in effects["mutations"]}

    # Repair rail: the resource's own final state plus the repair events,
    # reading nothing about what did or did not reach the destination.
    repaired_after_drift = any(
        e.get("kind") == "resource-repaired"
        and e.get("resource_id") == RESOURCE_ID
        and e["tick"] > drift_tick
        for e in events)
    conforms = resource["state"] == resource["required_state"]
    repair_reachable = repaired_after_drift and conforms

    outcomes = [e.get("outcome") for e in attempts]
    repair_reached = [e.get("repair_reached") for e in attempts]
    anomaly_reported = any(e.get("kind") == "precondition-anomaly-reported"
                           and e.get("resource_id") == RESOURCE_ID
                           for e in events)
    verdict = "pass" if (at_destination and repair_reachable) else "violation"
    return {
        "name": "precondition-is-repairable",
        "description": description,
        "expected": {"effect_at_destination": True,
                     "repair_ran_after_the_drift": True,
                     "resource_conforms_at_end_of_run": True},
        "observed": {"effect_at_destination": at_destination,
                     "repair_ran_after_the_drift": repaired_after_drift,
                     "resource_state": resource["state"],
                     "required_state": resource["required_state"],
                     "resource_conforms_at_end_of_run": conforms,
                     "attempt_outcomes": outcomes,
                     "repair_reached_per_attempt": repair_reached,
                     "anomaly_reported": anomaly_reported},
        "detail": {"prior_write_tick": prior_tick, "drift_tick": drift_tick,
                   "attempt_ticks": [e["tick"] for e in attempts],
                   "progress": at_destination,
                   "repair_reachable": repair_reachable},
        "verdict": verdict,
    }


@dataclass(frozen=True)
class DrillSpec:
    barrier: str
    fault_kind: str
    oracle: object
    post_fault_ops: tuple = field(default_factory=tuple)
    fault_target: str | None = None


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
    "child-completes-after-join": DrillSpec(
        barrier="before-join",
        fault_kind="expire_lease",
        oracle=_oracle_child_after_join,
        post_fault_ops=(("advance_generation", {"work_id": WORK_ID}),),
        fault_target=STRAGGLER_ID,
    ),
    "request-accepted-effect-never-applied": DrillSpec(
        barrier="request-accepted",
        fault_kind="drop_event",
        oracle=_oracle_request_never_applied,
    ),
    "source-advances-view-answers-anyway": DrillSpec(
        barrier="view-current",
        fault_kind="drop_event",
        oracle=_oracle_stale_view,
    ),
    "state-changes-check-does-not": DrillSpec(
        barrier="check-registered",
        fault_kind="drop_event",
        oracle=_oracle_check_falsifiability,
    ),
    "guard-refuses-repair-never-runs": DrillSpec(
        barrier="resource-conforming",
        fault_kind="drift_resource",
        oracle=_oracle_guard_before_repair,
        fault_target=RESOURCE_ID,
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

    fault: dict = {"kind": spec.fault_kind, "at_barrier": spec.barrier}
    if spec.fault_target is not None:
        fault["target"] = spec.fault_target

    try:
        send("seed", scenario=drill, mode=mode)
        send("start")
        send("wait_for_barrier", name=spec.barrier)
        send("inject_fault", **fault)
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
        "fault": fault,
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
        prog="python3 src/adapters/in_memory/run_drill.py",
        description="Run one reliability drill against the in-memory "
                    "reference factory.")
    parser.add_argument("drill", choices=sorted(DRILLS))
    parser.add_argument("--mode", required=True, choices=list(MODES))
    parser.add_argument("--out-dir", default="out/evidence")
    args = parser.parse_args(argv)
    return run_drill(args.drill, args.mode, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
