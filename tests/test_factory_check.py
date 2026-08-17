"""End-to-end tests for the factory-check CLI.

Every test drives the CLI as a subprocess, the same way the Makefile and
operators do, so exit codes and printed output are covered together.
"""

import collections
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "factory_check.py"
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


def test_validate_skips_the_estate_fixtures_by_name():
    """A code estate has no schema. Validate must say so rather than check
    it against the contract schema and report the mismatch as a defect, and
    it must not report the file as OK either."""
    estates = sorted(EXAMPLES.rglob("estate.yaml"))
    assert estates, "no estate fixtures found"
    result = run_cli("validate", *[str(p) for p in estates])
    assert result.returncode == 0, result.stdout + result.stderr
    for path in estates:
        assert f"{path}: SKIP (code estate;" in result.stdout, result.stdout
    assert f"{len(estates)} file(s) skipped" in result.stdout


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

sys.path.insert(0, str(ROOT / "src"))
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


# --- Rules with no coverage before 2026-08-16 --------------------------------
#
# Six rules were defined and exercised by nothing: AUTH-001, EFFECT-000,
# EFFECT-001, EFFECT-002, IDENT-001 and VERIFY-003. All six worked when probed
# by hand, which is the point -- nothing here would have noticed if they had
# stopped. AUTH-001 and IDENT-001 matter most: they are the two rules that
# separate a factory whose fence compares a monotonic generation from one whose
# fence compares a string a worker writes about itself, and that distinction is
# the kit's central claim.


def test_ident001_flags_undecided_identity_classes():
    doc = {"work": {"logical_identity": "unknown",
                    "attempt_identity": "unknown",
                    "session_identity": "unknown"}}
    assert "IDENT-001" in rule_ids(rules_mod.review(doc), "FAIL")


def test_ident001_passes_when_the_three_classes_are_distinct():
    doc = {"work": {"logical_identity": "work_item_id",
                    "attempt_identity": "attempt_id",
                    "session_identity": "session_id"}}
    assert "IDENT-001" not in rule_ids(rules_mod.review(doc), "FAIL")


def test_ident001_flags_classes_that_are_not_distinct():
    """One value reused for three roles cannot tell the roles apart."""
    doc = {"work": {"logical_identity": "work_item_id",
                    "attempt_identity": "work_item_id",
                    "session_identity": "work_item_id"}}
    assert "IDENT-001" in rule_ids(rules_mod.review(doc), "FAIL")


def test_auth001_requires_both_a_generation_and_a_lease():
    """Either half alone leaves the stale-writer window open."""
    generation_only = {"work": {"ownership": {"generation": "claim_generation"}}}
    lease_only = {"work": {"ownership": {"lease_expiry": "lease_expires_at"}}}
    both = {"work": {"ownership": {"generation": "claim_generation",
                                   "lease_expiry": "lease_expires_at"}}}
    assert "AUTH-001" in rule_ids(rules_mod.review(generation_only), "FAIL")
    assert "AUTH-001" in rule_ids(rules_mod.review(lease_only), "FAIL")
    assert "AUTH-001" not in rule_ids(rules_mod.review(both), "FAIL")


def test_auth001_treats_an_undecided_generation_as_absent():
    doc = {"work": {"ownership": {"generation": "unknown",
                                  "lease_expiry": "lease_expires_at"}}}
    assert "AUTH-001" in rule_ids(rules_mod.review(doc), "FAIL")


def test_effect000_flags_a_factory_that_declares_no_effects():
    assert "EFFECT-000" in rule_ids(rules_mod.review({"work": {}}), "WARN")


def test_effect001_and_effect002_flag_undecided_identity_and_retry():
    doc = {"effects": [{"name": "notify", "destination": "messaging",
                        "effect_identity": "unknown",
                        "retry_contract": "unknown"}]}
    fails = rule_ids(rules_mod.review(doc), "FAIL")
    assert "EFFECT-001" in fails and "EFFECT-002" in fails


def test_effect002_accepts_only_the_decided_retry_contracts():
    for contract in ("deduplicate", "converge", "reconcile"):
        doc = {"effects": [{"name": "notify", "destination": "messaging",
                            "effect_identity": "notification_id",
                            "retry_contract": contract,
                            "unknown_state_policy": "reconcile_then_block"}]}
        assert "EFFECT-002" not in rule_ids(rules_mod.review(doc), "FAIL"), contract


def test_verify003_flags_a_missing_verification_block():
    doc = {"artifacts": {"identity": "commit_sha"}}
    assert "VERIFY-003" in rule_ids(rules_mod.review(doc), "WARN")


# --- Reference-example output is pinned --------------------------------------
#
# The kit shipped a published count ("six failures and nine warnings") that went
# stale the moment CODE-000 was added in 03e36b8: nothing bound the documented
# numbers to the rules that produce them, so the drift was silent and was found
# only by someone re-running the example against the article a week later. That
# is the kit's own thesis turned on the kit -- a claim with no mechanism that
# could refute it. These tests are that mechanism. When a rule change moves a
# reference example, this suite fails until QUICKSTART.md and any published
# write-up are updated to match.

EXPECTED_EXAMPLE_FINDINGS = {
    "issue-to-pr/factory.yaml": {"FAIL": {}, "WARN": {}},
    "cross-repo-migration/factory.yaml": {"FAIL": {}, "WARN": {}},
    "long-running-agent/factory.yaml": {"FAIL": {}, "WARN": {}},
    "minimal-factory.yaml": {
        "FAIL": {"AUTH-002": 2, "EFFECT-002": 1, "EFFECT-003": 1, "VERIFY-002": 1},
        "WARN": {"AUTH-000": 1, "FLEET-001": 1, "FLEET-002": 1, "FLEET-003": 1,
                 "OBS-001": 1, "RECON-001": 1, "RECON-002": 1, "VERIFY-003": 1},
    },
    "unsafe-factory.yaml": {
        "FAIL": {"AUTH-002": 2, "CAMP-001": 1, "EFFECT-003": 1,
                 "VERIFY-001": 1, "VERIFY-002": 1},
        "WARN": {"CODE-000": 1, "EFFECT-004": 1, "FLEET-001": 1, "FLEET-002": 1,
                 "FLEET-003": 1, "IDENT-002": 1, "OBS-001": 1, "RECON-001": 2,
                 "RECON-002": 1},
    },
}

# Codes alone would not have caught the drift this guard exists for: a rule that
# starts firing twice where it fired once moves the published total without
# changing the set. Pin the multiplicity.


def _example_path(relative):
    return EXAMPLES / relative


def test_every_example_has_a_pinned_expectation():
    """A new example must state its expected findings, not appear untested."""
    on_disk = {str(p.relative_to(EXAMPLES)) for p in EXAMPLES.rglob("factory.yaml")}
    on_disk |= {p.name for p in EXAMPLES.glob("*.yaml")}
    assert on_disk == set(EXPECTED_EXAMPLE_FINDINGS), (
        "examples on disk and pinned expectations disagree; "
        "add the new example to EXPECTED_EXAMPLE_FINDINGS")


def test_reference_example_findings_are_exactly_as_documented(tmp_path):
    for relative, expected in EXPECTED_EXAMPLE_FINDINGS.items():
        result = run_cli("review", str(_example_path(relative)),
                         "--out", str(tmp_path / relative.replace("/", "_")))
        for severity, counts in expected.items():
            actual = collections.Counter(
                line.split()[1] for line in finding_lines(result.stdout, severity))
            assert dict(actual) == counts, (
                f"{relative}: {severity} findings drifted.\n"
                f"  expected {sorted(counts.items())}\n"
                f"  actual   {sorted(actual.items())}\n"
                "Update EXPECTED_EXAMPLE_FINDINGS *and* every document that "
                "quotes these counts (QUICKSTART.md, published write-ups).")
        total = sum(sum(c.values()) for c in expected.values())
        assert f"{sum(expected['FAIL'].values())} FAIL, " \
               f"{sum(expected['WARN'].values())} WARN" in result.stdout, \
            f"{relative}: summary line disagrees with the per-rule pins ({total} pinned)"


# Pages that print a literal review summary for an example. A rule change
# that moves a count has to move the prose with it, or this fails.
PUBLISHED_COUNTS = {
    "unsafe-factory.yaml": ("QUICKSTART.md", "examples/README.md",
                            "docs/contract-reference.md"),
    "minimal-factory.yaml": ("examples/README.md",),
}


def _summary_line(result):
    summary = [line for line in result.stdout.splitlines()
               if line.endswith("WARN") and "FAIL," in line]
    assert len(summary) == 1, result.stdout
    return summary[0]


def test_published_counts_match_the_examples(tmp_path):
    """Every page quoting a literal count stays honest against the rules."""
    for example, pages in PUBLISHED_COUNTS.items():
        result = run_cli("review", str(EXAMPLES / example),
                         "--out", str(tmp_path / example))
        summary = _summary_line(result)
        for page in pages:
            assert summary in (ROOT / page).read_text(), (
                f"{page} does not contain the current summary line "
                f"{summary!r} for {example}")


def test_contract_reference_documents_every_rule():
    """A new rule id is undocumented until the reference page names it."""
    catalog = set(re.findall(r'"([A-Z]+-\d{3})"',
                             (ROOT / "src" / "rules.py").read_text()))
    assert catalog, "no rule ids found in src/rules.py"
    reference = (ROOT / "docs" / "contract-reference.md").read_text()
    documented = set(re.findall(r'`([A-Z]+-\d{3})`', reference))
    assert catalog - documented == set(), (
        "rules with no entry in docs/contract-reference.md: "
        f"{sorted(catalog - documented)}")
    assert documented - catalog == set(), (
        "docs/contract-reference.md names rules that do not exist: "
        f"{sorted(documented - catalog)}")


def test_recon001_reports_once_per_destination_not_once_per_effect():
    """Three effects at one uncovered destination are one coverage problem."""
    doc = {"effects": [
        {"name": "push", "destination": "code_host"},
        {"name": "open_pr", "destination": "code_host"},
        {"name": "merge_pr", "destination": "code_host"},
        {"name": "notify", "destination": "messaging"},
    ]}
    recon = [f for f in rules_mod.review(doc) if f.rule == "RECON-001"]
    assert len(recon) == 2, [f.message for f in recon]
    destinations = sorted(f.message.split("destination ")[1].split(";")[0]
                          for f in recon)
    assert destinations == ["code_host", "messaging"]
    code_host = next(f for f in recon if "code_host" in f.message)
    for name in ("push", "open_pr", "merge_pr"):
        assert name in code_host.message, "the message should name every affected effect"
