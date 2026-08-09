"""End-to-end tests for the factory-check CLI.

Every test drives the CLI as a subprocess, the same way the Makefile and
operators do, so exit codes and printed output are covered together.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "cmd" / "factory-check" / "factory_check.py"
EXAMPLES = ROOT / "examples"
ISSUE_TO_PR = EXAMPLES / "issue-to-pr" / "factory.yaml"
UNSAFE = EXAMPLES / "unsafe-factory.yaml"

RENDERED_FILES = [
    "authority-map.mmd",
    "work-lifecycle.mmd",
    "effect-matrix.md",
    "guarantee-ledger.md",
    "promise-map.md",
]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, check=False)


def finding_lines(stdout, severity):
    return [line for line in stdout.splitlines() if line.startswith(severity + " ")]


def finding_rules(stdout, severity):
    return {line.split()[1] for line in finding_lines(stdout, severity)}


def test_validate_accepts_every_example():
    files = sorted(str(p) for p in EXAMPLES.rglob("*.yaml"))
    assert files, "no example files found"
    result = run_cli("validate", *files)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INVALID" not in result.stdout


def test_validate_rejects_invalid_file(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: factory.reliability/v1\nfactory: {}\n")
    result = run_cli("validate", str(bad))
    assert result.returncode != 0
    assert "INVALID" in result.stdout


def test_review_issue_to_pr_is_clean(tmp_path):
    result = run_cli("review", str(ISSUE_TO_PR), "--out", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert finding_lines(result.stdout, "FAIL") == []
    assert finding_lines(result.stdout, "WARN") == []
    strict = run_cli("review", str(ISSUE_TO_PR), "--strict", "--out", str(tmp_path))
    assert strict.returncode == 0


def test_review_strict_fails_on_warn(tmp_path):
    minimal = EXAMPLES / "minimal-factory.yaml"
    result = run_cli("review", str(minimal), "--strict", "--out", str(tmp_path))
    assert result.returncode == 1
    assert finding_lines(result.stdout, "WARN")


def test_review_unsafe_golden_rule_multiset(tmp_path):
    """Exact rule-id multiset for the normative unsafe example, so a rule
    that silently stops firing is caught, not just a shrinking subset."""
    result = run_cli("review", str(UNSAFE), "--out", str(tmp_path))
    assert result.returncode == 1
    findings = json.loads((tmp_path / "findings.json").read_text())
    fails = sorted(f["rule"] for f in findings if f["severity"] == "FAIL")
    warns = sorted(f["rule"] for f in findings if f["severity"] == "WARN")
    assert fails == ["AUTH-002", "AUTH-002", "CAMP-001", "EFFECT-003",
                     "VERIFY-001", "VERIFY-002"]
    assert warns == ["CODE-000", "EFFECT-004", "FLEET-001", "FLEET-002",
                     "FLEET-003", "IDENT-002", "OBS-001", "RECON-001",
                     "RECON-001", "RECON-002"]


def test_review_unsafe_factory_findings(tmp_path):
    result = run_cli("review", str(UNSAFE), "--out", str(tmp_path))
    assert result.returncode == 1
    fails = finding_rules(result.stdout, "FAIL")
    warns = finding_rules(result.stdout, "WARN")
    assert {"AUTH-002", "VERIFY-001", "VERIFY-002", "CAMP-001"} <= fails, fails
    expected_warns = {"IDENT-002", "EFFECT-004", "RECON-001", "RECON-002", "FLEET-002"}
    assert expected_warns <= warns, warns
    assert "EFFECT-003" in fails


def test_findings_json_matches_printed_output(tmp_path):
    result = run_cli("review", str(UNSAFE), "--out", str(tmp_path))
    findings = json.loads((tmp_path / "findings.json").read_text())
    assert isinstance(findings, list) and findings
    for entry in findings:
        assert set(entry) == {"rule", "severity", "message", "hint", "path"}
    printed = (finding_lines(result.stdout, "FAIL")
               + finding_lines(result.stdout, "WARN"))
    assert len(findings) == len(printed)
    printed_pairs = sorted(tuple(line.split()[:2]) for line in printed)
    json_pairs = sorted((f["severity"], f["rule"]) for f in findings)
    assert printed_pairs == json_pairs


def test_render_produces_five_nonempty_files(tmp_path):
    result = run_cli("render", str(ISSUE_TO_PR), "--out", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    for name in RENDERED_FILES:
        path = tmp_path / name
        assert path.is_file(), f"missing {name}"
        assert path.read_text().strip(), f"empty {name}"


def test_render_guarantee_ledger_shows_evidence_status(tmp_path):
    run_cli("render", str(ISSUE_TO_PR), "--out", str(tmp_path))
    ledger = (tmp_path / "guarantee-ledger.md").read_text()
    assert "AUTH-STALE-PUBLISH" in ledger
    assert "fault-tested" in ledger
    assert "EFFECT-DEDUP-PR" in ledger
    assert "enforced" in ledger


def test_render_without_guarantees_says_none_recorded(tmp_path):
    run_cli("render", str(UNSAFE), "--out", str(tmp_path))
    ledger = (tmp_path / "guarantee-ledger.md").read_text()
    assert "no guarantees recorded" in ledger


def test_render_promise_map_lists_missing_promises(tmp_path):
    run_cli("render", str(UNSAFE), "--out", str(tmp_path))
    promise_map = (tmp_path / "promise-map.md").read_text()
    assert "verified_to_published" in promise_map
    assert "published_to_acknowledged" in promise_map


def test_init_writes_valid_starter_contract(tmp_path):
    result = run_cli("init", "--out", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    starter = tmp_path / "factory.yaml"
    assert starter.is_file()
    validate = run_cli("validate", str(starter))
    assert validate.returncode == 0, validate.stdout + validate.stderr
    rerun = run_cli("init", "--out", str(tmp_path))
    assert rerun.returncode == 1


def test_review_rejects_schema_invalid_contract(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: factory.reliability/v1\nfactory: {}\n")
    result = run_cli("review", str(bad), "--out", str(tmp_path))
    assert result.returncode == 2


# Regression tests for the 2026-08-09 review findings. Each pins a hole
# that the original suite let coexist with 47 green tests.

sys.path.insert(0, str(ROOT / "cmd" / "factory-check"))
import rules as rules_mod  # noqa: E402


def rule_ids(findings, severity=None):
    return {f.rule for f in findings if severity is None or f.severity == severity}


def test_effect003_flags_undecided_unknown_policy():
    doc = {"effects": [{"name": "e", "destination": "d",
                        "effect_identity": "eid",
                        "retry_contract": "deduplicate",
                        "unknown_state_policy": "unknown"}]}
    findings = rules_mod.review(doc)
    assert "EFFECT-003" in rule_ids(findings, "FAIL")


def test_fleet_rules_treat_unknown_limits_as_undecided():
    doc = {"scheduling": {"classes": {
        "recovery": {"maximum": "unknown"},
        "interactive": {"reserved": "unknown"}}}}
    findings = rules_mod.review(doc)
    warns = rule_ids(findings, "WARN")
    assert "FLEET-001" in warns and "FLEET-002" in warns


def test_mutable_artifact_identity_and_self_report_verification_fail():
    doc = {"artifacts": {"identity": "branch_name",
                         "verification": {"identity": "worker_self_report",
                                          "binds_to": "branch_name"}}}
    findings = rules_mod.review(doc)
    fails = rule_ids(findings, "FAIL")
    assert "VERIFY-004" in fails and "VERIFY-005" in fails


def test_missing_authorities_and_code_estate_are_findings():
    doc = {"campaigns": {"completion": {
        "all_current_targets_have_disposition": ["published"]}}}
    findings = rules_mod.review(doc)
    warns = rule_ids(findings, "WARN")
    assert "AUTH-000" in warns and "CODE-000" in warns


def test_reconciliation_destination_field_is_authoritative():
    effect = {"name": "notify", "destination": "messaging"}
    covering = {"fact": "anything", "query": "poll", "destination": "messaging"}
    other = {"fact": "messaging_failure_counter", "query": "local_counter",
             "destination": "metrics_store"}
    assert rules_mod.entry_covers_effect(covering, effect)
    assert not rules_mod.entry_covers_effect(other, effect)


def test_init_accepts_positional_target(tmp_path):
    target = tmp_path / "my-factory.yaml"
    result = run_cli("init", str(target))
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.exists()


def test_validate_rejects_bad_calendar_date(tmp_path):
    bad = tmp_path / "guarantee.yaml"
    bad.write_text(
        "id: G\nclaim: c\nowner: o\n"
        "mechanism: {type: t, inputs: [work_id]}\n"
        "oracle: {type: q, assertion: a}\n"
        "evidence: {status: enforced, last_verified: '2026-99-99'}\n")
    result = run_cli("validate", str(bad))
    assert result.returncode != 0
    assert "date" in result.stdout


def test_fault_tested_requires_evidence_fields(tmp_path):
    bare = tmp_path / "guarantee.yaml"
    bare.write_text(
        "id: G\nclaim: c\nowner: o\n"
        "mechanism: {type: t, inputs: [work_id]}\n"
        "oracle: {type: q, assertion: a}\n"
        "evidence: {status: fault-tested}\n")
    result = run_cli("validate", str(bare))
    assert result.returncode != 0


def test_standalone_effect_doc_selects_effect_schema(tmp_path):
    doc = tmp_path / "effect.yaml"
    doc.write_text(
        "name: create_pull_request\ndestination: code_host\n"
        "effect_identity: pr_op_id\nretry_contract: reconcile\n"
        "readback: find_by_head_reference\n"
        "unknown_state_policy: block_and_escalate\n")
    result = run_cli("validate", str(doc))
    assert result.returncode == 0, result.stdout
    assert "effect.schema.json" in result.stdout


def test_authority_map_reconciles_only_covered_destinations(tmp_path):
    contract = tmp_path / "factory.yaml"
    contract.write_text(
        "version: factory.reliability/v1\n"
        "factory: {name: t}\n"
        "work:\n"
        "  logical_identity: work_id\n"
        "  attempt_identity: attempt_id\n"
        "  session_identity: session_id\n"
        "effects:\n"
        "  - {name: pr, destination: code_host, effect_identity: pr_id,\n"
        "     retry_contract: reconcile, readback: rb,\n"
        "     unknown_state_policy: block_and_escalate}\n"
        "  - {name: notify, destination: messaging, effect_identity: n_id,\n"
        "     retry_contract: deduplicate,\n"
        "     unknown_state_policy: block_and_escalate}\n"
        "reconciliation:\n"
        "  - {fact: pull_request_state, query: read_current_pull_request,\n"
        "     destination: code_host, interval: 5m}\n")
    result = run_cli("render", str(contract), "--out", str(tmp_path / "out"))
    assert result.returncode == 0, result.stdout + result.stderr
    graph = (tmp_path / "out" / "authority-map.mmd").read_text()
    assert "ext_code_host -.->|reconciled into|" in graph
    assert "ext_messaging -.->|reconciled into|" not in graph


def test_guarantee_ledger_reports_missing_evidence(tmp_path):
    contract = tmp_path / "factory.yaml"
    contract.write_text("version: factory.reliability/v1\nfactory: {name: t}\n")
    gdir = tmp_path / "guarantees"
    gdir.mkdir()
    (gdir / "g.yaml").write_text(
        "id: G\nclaim: c\nowner: o\n"
        "mechanism: {type: t, inputs: [work_id]}\n"
        "oracle: {type: q, assertion: a}\n"
        "evidence: {status: fault-tested, drill: no/such/drill.md,\n"
        "  last_verified: '2026-08-09', artifact: no/such/evidence.json}\n")
    result = run_cli("render", str(contract), "--out", str(tmp_path / "out"))
    assert result.returncode == 0, result.stdout + result.stderr
    ledger = (tmp_path / "out" / "guarantee-ledger.md").read_text()
    assert "EVIDENCE MISSING" in ledger
    assert "path does not resolve" in ledger


def test_published_manifest_requires_artifact_fields(tmp_path):
    import jsonschema
    schema = json.loads((ROOT / "schemas" / "work-manifest.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    manifest = {"work_id": "w", "attempt_id": "a", "generation": 1,
                "repository": "r", "base_revision": "abc",
                "publication_state": "published"}
    errors = list(validator.iter_errors(manifest))
    assert errors, "published manifest with no artifact fields validated"
