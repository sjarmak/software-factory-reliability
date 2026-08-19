"""End-to-end tests for the factory-check CLI.

Every test drives the CLI as a subprocess, the same way the Makefile and
operators do, so exit codes and printed output are covered together.
"""

import collections
import json
import re
import subprocess
import sys

import pytest
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
                     "FLEET-003", "IDENT-002", "OBS-001", "OBS-002",
                     "RECON-001", "RECON-001", "RECON-002"]


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
    for contract in ("deduplicate", "converge", "reconcile", "at_least_once"):
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
                 "FLEET-003": 1, "IDENT-002": 1, "OBS-001": 1, "OBS-002": 1,
                 "RECON-001": 2, "RECON-002": 1},
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
#
# This list is curated on purpose and covers ONE direction: these pages must
# keep carrying a count. It cannot cover the other direction, because a page
# that quotes a count and is not listed here is exactly the case a hand-written
# list does not know about -- which is the drift the whole guard exists for,
# one level up. test_no_page_quotes_a_summary_that_is_no_longer_true below is
# the other rail, and it enumerates the pages from disk instead.
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


# A review summary quoted in prose, anywhere. The marker excuses a count that
# is deliberately out of date -- QUICKSTART cites the v0.1 figure to explain
# why the current one differs, and that citation is the point of the sentence.
# It is an explicit comment rather than a typographic convention (bold, a
# blockquote) because a formatting choice made for other reasons would excuse a
# genuinely stale number by accident, and a guard that excuses its own subject
# is not a guard.
_SUMMARY_IN_PROSE = re.compile(r"\d+ FAIL, \d+ WARN")
_HISTORICAL_MARKER = "<!-- historical -->"


def _documentation_pages():
    """Every .md in the repository except generated output."""
    return sorted(p for p in ROOT.rglob("*.md")
                  if not any(part == "out" or part.startswith(".")
                             for part in p.relative_to(ROOT).parts))


def test_no_page_quotes_a_summary_that_is_no_longer_true(tmp_path):
    """A count in prose is either currently true or marked as history.

    The pages are enumerated from disk rather than listed, so a document that
    starts quoting a count is covered the day it is written. PUBLISHED_COUNTS
    above cannot do this: an unlisted page is silently uncovered there, and the
    counts it carries are the ones nobody re-ran.

    What this does NOT check is attribution: it asks whether some example still
    produces the quoted figure, not whether the page names the right example.
    A page swapping `5 FAIL, 8 WARN` onto the unsafe contract passes here and
    fails in PUBLISHED_COUNTS, which is why both rails are kept.
    """
    live = set()
    for relative in EXPECTED_EXAMPLE_FINDINGS:
        result = run_cli("review", str(_example_path(relative)),
                         "--out", str(tmp_path / relative.replace("/", "_")))
        live.add(_summary_line(result))

    pages = _documentation_pages()
    assert pages, "no documentation pages found; the walk is broken"
    stale = []
    for page in pages:
        for number, line in enumerate(page.read_text().splitlines(), 1):
            if _HISTORICAL_MARKER in line:
                continue
            for quoted in _SUMMARY_IN_PROSE.findall(line):
                if quoted not in live:
                    stale.append(f"{page.relative_to(ROOT)}:{number}: {quoted!r} "
                                 f"in {line.strip()[:70]!r}")
    assert not stale, (
        "documentation quotes review summaries no example produces:\n  "
        + "\n  ".join(stale)
        + "\n\nRe-run the example and correct the page, or mark the line "
          f"{_HISTORICAL_MARKER} if the figure is cited as history.\n"
          "Current summaries: " + ", ".join(sorted(live)))


def test_the_historical_marker_is_the_only_thing_excusing_a_stale_count(tmp_path):
    """The escape hatch is narrow: nothing else on a line excuses a figure."""
    pages = _documentation_pages()
    marked = [line for page in pages for line in page.read_text().splitlines()
              if _HISTORICAL_MARKER in line and _SUMMARY_IN_PROSE.search(line)]
    assert marked, (
        "no page cites a historical count, so the escape hatch in "
        "test_no_page_quotes_a_summary_that_is_no_longer_true is untested; "
        "either it is unused and should be deleted, or the marker moved")


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


RECONCILE_PROBES = """\
version: factory.probes/v1
name: lanes
factory_name: lanes
scan:
  include_globs: ["bin/*", "formulas/*.toml"]
effects:
  - name: slack_publish
    destination: messaging
    call_site:
      scripted:
        path_globs: ["bin/*"]
        any_of:
          - regex: 'slack_post'
      instructed:
        path_globs: ["formulas/*.toml"]
        any_of:
          - regex: 'slack_post'
    identity:
      name: idempotency_key
      markers: ["--idempotency-key"]
  - name: merge_pull_request
    destination: code_host
    call_site:
      scripted:
        path_globs: ["bin/*"]
        any_of:
          - regex: 'gh pr merge'
      instructed:
        path_globs: ["formulas/*.toml"]
        any_of:
          - regex: 'gh pr merge'
    identity:
      name: pr_head_sha
      markers: ["--match-head-commit"]
"""

RECONCILE_CONTRACT = """\
version: factory.reliability/v1
factory:
  name: lanes
effects:
  - name: slack_publish
    destination: messaging
    effect_identity: idempotency_key
    retry_contract: deduplicate
    unknown_state_policy: block_and_escalate
  - name: merge_pull_request
    destination: code_host
    effect_identity: pr_head_sha
    retry_contract: converge
    unknown_state_policy: block_and_escalate
"""


def _lanes_install(tmp_path):
    """An installation with one effect done in code and one only by agents."""
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "formulas").mkdir(parents=True)
    (root / "bin" / "publish").write_text(
        "#!/usr/bin/env bash\nslack_post --idempotency-key \"$KEY\" \"$MSG\"\n")
    # Both effects are ALSO named in agent instructions, which is the normal
    # shape of an agent-driven factory and the case that used to collapse every
    # verdict into one.
    (root / "formulas" / "ship.toml").write_text(
        'prompt = """\nPost with slack_post, then run gh pr merge on the PR.\n"""\n')
    contract = tmp_path / "factory.yaml"
    contract.write_text(RECONCILE_CONTRACT)
    probes = tmp_path / "probes.yaml"
    probes.write_text(RECONCILE_PROBES)
    return contract, probes, root


def test_reconcile_confirms_the_code_lane_and_still_prints_the_residual(tmp_path):
    """An instructed site must not erase a code lane that carries the identity.

    Before this, derived_identity's strict rule (any instructed site withdraws
    the identity) was applied to reconciliation too, so on any agent-driven
    factory every declared effect read DRIFT and `confirmed` was unreachable --
    a uniform answer across inputs that must differ. The residual travels with
    the confirmation, so nobody reads it as a static guarantee.
    """
    contract, probes, root = _lanes_install(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines() if l.startswith("CONFIRMED  slack_publish")]
    assert line, out.stdout
    assert "all 1 code call site(s) carry it" in line[0]
    assert "1 further call site(s) are agent instructions" in line[0]


def test_reconcile_calls_an_agents_only_effect_unverified_not_drift(tmp_path):
    """No code performing the effect is a different problem from code that
    performs it without the identity, and it needs a different fix."""
    contract, probes, root = _lanes_install(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines()
            if l.startswith("UNVERIFIED  merge_pull_request")]
    assert line, out.stdout
    assert "no code performs this effect" in line[0]
    assert not [l for l in out.stdout.splitlines() if l.startswith("DRIFT  merge_pull_request")]


def test_reconcile_contradicts_a_false_declared_instructed_count(tmp_path):
    """The check that stops the new field from being a self-clearing one.

    EFFECT-006 fails on a nonzero instructed_call_sites, and review reads the
    CONTRACT. So an author who does not want that finding writes 0 -- and
    review goes green with the installation untouched, which is the hand-edit-
    the-declaration move this whole tool exists to catch, reintroduced by the
    field added to catch it. Only the scan can contradict it.

    Mutation: drop the _check_declared_observations call from cmd_reconcile, or
    make it compare only when the declared count is larger.
    """
    contract, probes, root = _lanes_install(tmp_path)
    contract.write_text(contract.read_text().replace(
        "    effect_identity: idempotency_key",
        "    effect_identity: idempotency_key\n    instructed_call_sites: 0", 1))
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines() if l.startswith("STALE  slack_publish")]
    assert line, out.stdout
    assert "declares instructed_call_sites 0" in line[0]
    assert "the scan finds 1" in line[0]
    assert out.returncode == 1


def test_reconcile_reports_an_unmeasured_count_without_failing_on_it(tmp_path):
    """Absence is not contradiction, and it is not a silent pass.

    An omitted instructed_call_sites means the author never measured, which is
    honest. It also means EFFECT-006 can never fire on that contract, because
    review reads the document -- so reconcile is the only place that knows the
    real number and it has to say it. It does not set the exit code: failing an
    omission would punish the contract that declines to guess over the one that
    writes a comfortable zero, which the STALE check catches instead.

    Mutation: report the omission as STALE, or drop the elif branch entirely.
    """
    contract, probes, root = _lanes_install(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines()
            if l.startswith("UNRECORDED  slack_publish")]
    assert line, out.stdout
    assert "1 agent-instruction call site(s)" in line[0]
    assert not [l for l in out.stdout.splitlines() if l.startswith("STALE")], out.stdout
    # The half the name promises, and the half a mutation walked straight
    # through until it was written down: reported, and NOT failed on.
    assert out.returncode == 0, out.stdout


def test_a_measured_zero_is_not_reported_as_unmeasured(tmp_path):
    """The other rail on the branch above: it keys on the field being ABSENT,
    not on the count being uninteresting. An effect with no instructed sites and
    no declaration has nothing to report, and reporting it would put a line on
    every fully-scripted effect in every contract.

    Mutation: fire the UNRECORDED branch on `not declared_count` rather than on
    `declared_count is None`.
    """
    contract, probes, root = _lanes_install(tmp_path)
    # Remove the one instructed site, leaving the scripted call alone.
    (root / "formulas" / "ship.toml").write_text('prompt = "ship it"\n')
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    assert not [l for l in out.stdout.splitlines()
                if l.startswith(("STALE", "UNRECORDED"))], out.stdout


def test_reconcile_contradicts_a_false_declared_code_lane(tmp_path):
    """The second declared observation, checked the same way: an author can
    write code_lane_identity by hand too, and the scan is the authority."""
    contract, probes, root = _lanes_install(tmp_path)
    contract.write_text(contract.read_text().replace(
        "    effect_identity: idempotency_key",
        "    effect_identity: idempotency_key\n"
        "    code_lane_identity: request_uuid", 1))
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines() if l.startswith("STALE  slack_publish")]
    assert line, out.stdout
    assert "declares code_lane_identity request_uuid" in line[0]
    assert "the scan finds idempotency_key" in line[0]


def test_reconcile_still_reports_drift_when_the_code_lane_misses_the_identity(tmp_path):
    """The confirmation rail must be able to go red: the same installation with
    the identity marker removed from the one code site."""
    contract, probes, root = _lanes_install(tmp_path)
    (root / "bin" / "publish").write_text(
        "#!/usr/bin/env bash\nslack_post \"$MSG\"\n")
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines() if l.startswith("DRIFT  slack_publish")]
    assert line, out.stdout
    assert "1 of 1 code call site(s) carry no idempotency_key" in line[0]
    assert out.returncode == 1


def _tiny_git_install(tmp_path):
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    (root / "bin" / "ship").write_text(
        "#!/usr/bin/env bash\ngit push origin main\n")
    (root / "logs" / "run.log").write_text(
        "2026-08-18 ran: git push origin main\n" * 40)
    (root / ".gitignore").write_text("logs/\n")
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    return root


def test_probes_init_does_not_scaffold_globs_for_vcs_ignored_output(tmp_path):
    """Driven through the CLI, because the default was dead on that path.

    `survey` carried the right default and `cmd_probes_init` built its own scan
    dict and overrode it, so a unit test on the scanner passed while the only
    command anyone runs was unaffected. The generated pack is the artifact; it
    is what this asserts.
    """
    root = _tiny_git_install(tmp_path)
    pack = tmp_path / "probes.yaml"
    out = run_cli("probes-init", str(root), "--write", str(pack))
    assert out.returncode == 0, out.stdout + out.stderr
    text = pack.read_text()
    assert "'bin/*'" in text
    assert "logs/" not in text, text


def test_probes_init_can_be_told_to_read_ignored_paths(tmp_path):
    """The escape hatch exists and is the rail that proves the default is doing
    something, rather than the glob never being generated for another reason."""
    root = _tiny_git_install(tmp_path)
    pack = tmp_path / "probes.yaml"
    out = run_cli("probes-init", str(root), "--write", str(pack),
                  "--scan-ignored-paths")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "logs/" in pack.read_text()


def test_ident_002_reads_the_key_as_well_as_the_prose(tmp_path):
    """effect_identity_key exists so a composite identity can stay in prose and
    still be machine-checkable. A rule that reads only the prose lets an
    attempt-scoped identity in through the new field, and the reconciler then
    CONFIRMS it against call sites that faithfully carry an identity no
    destination can ever deduplicate on -- the strongest possible green for
    the exact defect the rule exists to catch.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "attempt-keyed.yaml"
    # The prose stays innocent; only the key is attempt-scoped.
    doc.write_text(clean.replace(
        "    effect_identity: notification_id",
        "    effect_identity: the (channel, thread) pair\n"
        "    effect_identity_key: attempt_id", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    ids = [line.split()[1] for line in result.stdout.splitlines()
           if line.startswith(("FAIL ", "WARN "))]
    assert "IDENT-002" in ids, result.stdout


def test_ident_002_reads_the_key_when_the_prose_is_undecided(tmp_path):
    """The worst of the two shapes, and the one a fix hung off the prose misses.

    An undecided effect_identity beside a decided attempt-scoped KEY is what
    reconcile actually compares, so the contract is attempt-keyed in the only
    field that reaches the call sites. Reporting only "the identity is missing"
    describes the weaker half and leaves the retry defect unnamed.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "unknown-prose-attempt-key.yaml"
    doc.write_text(clean.replace(
        "    effect_identity: notification_id",
        "    effect_identity: unknown\n"
        "    effect_identity_key: attempt_id", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    ids = [line.split()[1] for line in result.stdout.splitlines()
           if line.startswith(("FAIL ", "WARN "))]
    assert "IDENT-002" in ids, result.stdout
    assert "EFFECT-001" in ids, result.stdout


def test_the_effect_matrix_distinguishes_two_contracts_by_their_key(tmp_path):
    """Two contracts with identical prose and different keys are treated
    differently by reconcile and by IDENT-002. A matrix that renders only the
    prose shows them as the same row, so the artifact a human reviews cannot
    show the field the machine acts on."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "keyed.yaml"
    doc.write_text(clean.replace(
        "    effect_identity: notification_id",
        "    effect_identity: the (channel, thread) pair\n"
        "    effect_identity_key: notify_dedupe_key", 1))
    run_cli("render", str(doc), "--out", str(tmp_path))
    matrix = (tmp_path / "effect-matrix.md").read_text()
    assert "Identity key" in matrix
    assert "notify_dedupe_key" in matrix


def test_at_least_once_is_a_decided_value_not_an_undecided_one(tmp_path):
    """A destination with no dedup property leaves a builder three values that
    are all false and "unknown", which records "the builder has not decided"
    when the truth is "we decided and repeats duplicate". Declaring the bad
    answer must clear EFFECT-002 -- otherwise the honest record and the empty
    one read the same, and nobody writes the honest one."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "at-least-once.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    retry_contract: at_least_once\n"
        "    duplicate_disposition: a second copy posts as a new message", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    ids = [line.split()[1] for line in result.stdout.splitlines()
           if line.startswith(("FAIL ", "WARN "))]
    assert "EFFECT-002" not in ids, result.stdout
    assert "EFFECT-005" not in ids, result.stdout


def test_at_least_once_without_a_disposition_fails(tmp_path):
    """The other rail, and the reason the value is not just a green escape from
    EFFECT-002. Saying repeats duplicate and not saying what a duplicate costs
    is a decided answer with the consequence left off."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "at-least-once-bare.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    retry_contract: at_least_once", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    ids = [line.split()[1] for line in result.stdout.splitlines()
           if line.startswith("FAIL ")]
    assert "EFFECT-005" in ids, result.stdout
    # The finding has to name the field to add. A reader told only that the
    # value is wrong cannot tell what the tool wants written -- and the
    # machine-readable path is asserted too, because a finding whose printed
    # hint says duplicate_disposition while its structured path points at
    # retry_contract sends any tool built on findings.json to the wrong field.
    assert "duplicate_disposition" in result.stdout, result.stdout
    findings = json.loads((tmp_path / "findings.json").read_text())
    paths = [f["path"] for f in findings if f["rule"] == "EFFECT-005"]
    assert paths and all(p.endswith(".duplicate_disposition") for p in paths), paths


def test_an_undecided_disposition_is_not_a_disposition(tmp_path):
    """"unknown" is accepted by the schema everywhere a builder has not decided.
    A rule that only checks the key is present accepts it as an answer, which is
    the scaffold's own placeholder reading as a decision."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "at-least-once-unknown.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    retry_contract: at_least_once\n"
        "    duplicate_disposition: unknown", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    ids = [line.split()[1] for line in result.stdout.splitlines()
           if line.startswith("FAIL ")]
    assert "EFFECT-005" in ids, result.stdout


def test_a_disposition_without_at_least_once_fires_nothing(tmp_path):
    """EFFECT-005 is scoped to the contract value, not to the field. Documenting
    what a duplicate would do on an effect that claims the destination collapses
    repeats is extra honesty, and a rule scoped the other way round reads it as
    a contradiction and fires -- which teaches builders to write less."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "disposition-only.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    retry_contract: deduplicate\n"
        "    duplicate_disposition: cannot happen; the destination collapses it", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    assert "EFFECT-005" not in result.stdout, result.stdout


def test_the_matrix_spells_out_what_a_duplicate_costs(tmp_path):
    """A row reading at_least_once says only that a repeat lands. Without the
    bound beside it that reads as a shrug, and the disposition is the whole
    answer to what a retry costs at this destination."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "dup.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    retry_contract: at_least_once\n"
        "    duplicate_disposition: a second copy posts as a new message in the channel", 1))
    run_cli("render", str(doc), "--out", str(tmp_path))
    matrix = (tmp_path / "effect-matrix.md").read_text()
    assert "Effects that duplicate on a repeat" in matrix
    # The cost has to sit on the same line as the effect it belongs to. A
    # section listing three dispositions with no names attached tells a reader
    # that something duplicates and not which thing, which is not usable in the
    # incident this section exists for.
    priced = [line for line in matrix.splitlines()
              if "a second copy posts as a new message in the channel" in line]
    assert len(priced) == 1, matrix
    assert "send_notification" in priced[0], priced[0]


def test_the_duplicate_section_is_absent_when_nothing_duplicates(tmp_path):
    """The other rail. A heading that is always there stops meaning anything,
    and a reader scanning for it learns to skip it.

    Two fixtures, because one of them only proves the section can be absent. The
    second is the case that separates "selects on the retry contract" from
    "selects on the field being present": an effect whose destination collapses
    repeats may still document what a duplicate WOULD cost, and listing it under
    a heading that says it duplicates is a false statement about the system."""
    run_cli("render", str(ISSUE_TO_PR), "--out", str(tmp_path))
    matrix = (tmp_path / "effect-matrix.md").read_text()
    assert "Effects that duplicate on a repeat" not in matrix

    documented = tmp_path / "documented-but-collapsed.yaml"
    documented.write_text(ISSUE_TO_PR.read_text().replace(
        "    retry_contract: deduplicate",
        "    retry_contract: deduplicate\n"
        "    duplicate_disposition: cannot happen; the destination collapses it",
        1))
    out = tmp_path / "documented"
    run_cli("render", str(documented), "--out", str(out))
    matrix = (out / "effect-matrix.md").read_text()
    assert "Effects that duplicate on a repeat" not in matrix, matrix


def schema_enum(path, key):
    """The enum declared for one field in one schema, found wherever it sits.

    Located by walking rather than by a fixed JSON path, because the same field
    is declared standalone in effect.schema.json and inlined several levels down
    in factory.schema.json, and a hand-written path to each is one refactor away
    from reading the wrong node and silently agreeing with itself. The
    single-match assertion is the guard on that: two enums for one key means the
    walk found something other than what the caller meant.
    """
    node = json.loads((ROOT / "schemas" / path).read_text())
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            if key in obj and isinstance(obj[key], dict) and "enum" in obj[key]:
                found.append(frozenset(obj[key]["enum"]))
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(node)
    assert len(found) == 1, "%s: expected one %s enum, found %d" % (
        path, key, len(found))
    return set(found[0])


def test_the_three_definitions_of_the_retry_grammar_agree():
    """The set of retry contracts is written down three times: once in the rule
    module, once in the standalone effect schema, once inlined in the factory
    schema. Nothing makes a change to one propagate to the others, so a value
    added in the place a developer happens to open is accepted by one lane and
    refused by another. Asserting equality is the only thing that notices.

    "unknown" is in the schemas and deliberately out of the runtime's decided
    set -- that is what makes EFFECT-002 fire on it -- so the comparison adds it
    back rather than pretending the two sets are identical."""
    effect_enum = schema_enum("effect.schema.json", "retry_contract")
    factory_enum = schema_enum("factory.schema.json", "retry_contract")
    expected = rules_mod.ALLOWED_RETRY_CONTRACTS | {"unknown"}
    assert effect_enum == expected, effect_enum ^ expected
    assert factory_enum == expected, factory_enum ^ expected


def test_a_whitespace_disposition_is_not_a_disposition(tmp_path):
    """The hole a stripped predicate closes. A field holding one space is
    schema-valid (minLength counts the space) and, before _declared stripped,
    read as declared -- so the space bar cleared a rule whose entire purpose is
    to separate the builder who wrote the cost down from the builder who did
    not. " unknown " is the same hole with a different disguise."""
    clean = ISSUE_TO_PR.read_text()
    for label, value in (("space", " "), ("padded-unknown", " unknown ")):
        doc = tmp_path / ("at-least-once-%s.yaml" % label)
        doc.write_text(clean.replace(
            "    retry_contract: deduplicate",
            "    retry_contract: at_least_once\n"
            "    duplicate_disposition: \"%s\"" % value, 1))
        out = tmp_path / label
        result = run_cli("review", str(doc), "--out", str(out))
        ids = [line.split()[1] for line in result.stdout.splitlines()
               if line.startswith("FAIL ")]
        assert "EFFECT-005" in ids, "%s: %s" % (label, result.stdout)


def test_the_unsound_policies_are_writable_and_fail(tmp_path):
    """The rule that could not fire. Both schemas used to refuse assume_success
    and assume_failure, and review validates against the schema before it runs a
    single rule -- so EFFECT-003's unsound branch was unreachable: every
    document carrying the value it tests for was rejected upstream. The catalog
    listed the severity as live.

    Two halves, and the second is the one that matters. The value has to reach
    the rule (schema accepts it), and the rule has to fail it (green would be
    worse than the refusal it replaced)."""
    clean = ISSUE_TO_PR.read_text()
    for policy in ("assume_success", "assume_failure"):
        doc = tmp_path / ("policy-%s.yaml" % policy)
        doc.write_text(clean.replace(
            "    unknown_state_policy: block_and_escalate",
            "    unknown_state_policy: %s" % policy))
        out = tmp_path / policy
        result = run_cli("review", str(doc), "--out", str(out))
        assert "not a valid factory contract" not in result.stdout, \
            "%s: the schema still refuses the value, so the rule cannot see it\n%s" % (
                policy, result.stdout)
        ids = [line.split()[1] for line in result.stdout.splitlines()
               if line.startswith("FAIL ")]
        assert "EFFECT-003" in ids, "%s: %s" % (policy, result.stdout)
        # The structured path too. A finding whose printed hint talks about the
        # policy while its path points somewhere else sends every tool built on
        # findings.json to the wrong field.
        findings = json.loads((out / "findings.json").read_text())
        paths = [f["path"] for f in findings if f["rule"] == "EFFECT-003"]
        assert paths and all(q.endswith(".unknown_state_policy") for q in paths), paths


def test_the_two_unsound_policies_do_not_get_the_same_sentence(tmp_path):
    """They fail for opposite reasons: one loses the effect, the other sends it
    twice. A finding that recites both costs for whichever value it found leaves
    the reader to work out which half applies, and the two repairs are not the
    same repair."""
    clean = ISSUE_TO_PR.read_text()
    said = {}
    for policy in ("assume_success", "assume_failure"):
        doc = tmp_path / ("both-%s.yaml" % policy)
        doc.write_text(clean.replace(
            "    unknown_state_policy: block_and_escalate",
            "    unknown_state_policy: %s" % policy))
        out = tmp_path / ("both-" + policy)
        run_cli("review", str(doc), "--out", str(out))
        findings = json.loads((out / "findings.json").read_text())
        mine = [f for f in findings if f["rule"] == "EFFECT-003"]
        assert mine and all(f["severity"] == "FAIL" for f in mine), mine
        said[policy] = " ".join(f["message"] for f in mine)
    assert "loses the effect" in said["assume_success"], said["assume_success"]
    assert "duplicat" not in said["assume_success"], said["assume_success"]
    assert "second one" in said["assume_failure"], said["assume_failure"]
    assert "loses the effect" not in said["assume_failure"], said["assume_failure"]
    # Each cost has to name the branch it happens on, and they are opposite
    # branches. An earlier draft said assume_success loses the effect when the
    # attempt "may have landed" -- backwards, because if it landed then assuming
    # success is right. The loss is on did-NOT-land; the duplicate is on did.
    assert "did not land" in said["assume_success"], said["assume_success"]
    assert "did land" in said["assume_failure"], said["assume_failure"]


def test_the_unknown_state_policy_grammar_agrees_across_its_definitions():
    """Same three-definitions problem as the retry contract, and this is the
    field it already bit. The two schemas must offer the same values, and both
    must still accept the unsound ones -- a schema that quietly drops them puts
    EFFECT-003 back in the state where its branch cannot be reached, which is a
    regression no other test in this file would notice."""
    effect_enum = schema_enum("effect.schema.json", "unknown_state_policy")
    factory_enum = schema_enum("factory.schema.json", "unknown_state_policy")
    assert effect_enum == factory_enum, effect_enum ^ factory_enum
    # EQUALITY, not containment. Subset assertions were the first version of
    # this test and they let a value be added to both schemas and to nothing
    # else -- "silently_retry" would satisfy every subset here, pass the schema,
    # and review green, which is the exact hole the test claims to close.
    assert effect_enum == rules_mod.ALLOWED_UNKNOWN_STATE_POLICIES, \
        effect_enum ^ rules_mod.ALLOWED_UNKNOWN_STATE_POLICIES
    assert rules_mod.SOUND_POLICIES.isdisjoint(rules_mod.UNSOUND_POLICIES)


def test_an_unrecognised_policy_fails_rather_than_passing_by_default():
    """EFFECT-003 used to fail two named bad values and let everything else
    through, so "not one of the two we thought of" counted as sound. A value
    added to both schemas and to nothing else -- a fork, a merge, a good idea
    nobody finished -- would then review green while the rules had never
    reasoned about it.

    Driven through rules.review() rather than the CLI on purpose: the point is
    what happens when a value gets PAST the schema, so a fixture the schema
    rejects would be testing the wrong layer."""
    doc = {"effects": [{"name": "publish", "destination": "messaging",
                        "effect_identity": "publish_id",
                        "retry_contract": "deduplicate",
                        "unknown_state_policy": "silently_retry"}]}
    findings = rules_mod.review(doc)
    effect003 = [f for f in findings if f.rule == "EFFECT-003"]
    assert effect003, [f.rule for f in findings]
    assert effect003[0].severity == "FAIL", effect003[0]
    assert "silently_retry" in effect003[0].message, effect003[0].message


def test_the_rendered_matrix_marks_an_unsound_policy(tmp_path):
    """render does not run the rules, and it never did -- but while the two
    unsound values were schema-INVALID, render rejected any contract carrying
    one and the omission was invisible. Making them writable removed that
    accidental cover: render now exits 0 and writes every artifact.

    QUICKSTART calls the matrix "the artifact you hand to a reviewer who will
    not read YAML". A bare `assume_failure` in a cell reads to that reviewer as
    one more decided value beside `block_and_escalate`, and the person holding
    the printout is not the person running review."""
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "unsound.yaml"
    doc.write_text(clean.replace(
        "    unknown_state_policy: block_and_escalate",
        "    unknown_state_policy: assume_failure"))
    run_cli("render", str(doc), "--out", str(tmp_path))
    matrix = (tmp_path / "effect-matrix.md").read_text()
    rows = [line for line in matrix.splitlines()
            if line.startswith("|") and "assume_failure" in line]
    assert rows, matrix
    assert all("UNSOUND" in row for row in rows), rows
    assert "## Effects that resolve an ambiguous outcome by guessing" in matrix
    priced = [line for line in matrix.splitlines()
              if line.startswith("- ") and "assume_failure" in line]
    assert priced, matrix
    assert all("did land" in line for line in priced), priced


def test_the_unsound_section_is_absent_when_every_policy_is_sound(tmp_path):
    """The other rail. A heading that is always present stops carrying
    information, and the example fixture is the case that must not trip it."""
    run_cli("render", str(ISSUE_TO_PR), "--out", str(tmp_path))
    matrix = (tmp_path / "effect-matrix.md").read_text()
    assert "resolve an ambiguous outcome by guessing" not in matrix
    assert "UNSOUND" not in matrix


def test_a_decided_identity_with_instructed_sites_fails(tmp_path):
    """The finding that keeps a declared identity from over-reaching.

    An effect can declare an identity that its code really does carry at every
    scripted site, and still be performed by routes that are sentences in a
    prompt. The declaration is true of the code and the effect is still exposed:
    a retry through an instructed route can duplicate.

    FAIL, not WARN. This was WARN for one release, on the argument that the
    declaration is true and the remedy is a design change. Neither is a severity
    argument -- the catalog defines FAIL as an open failure boundary, this is
    one, and warnings do not fail a review without --strict, so the exposure
    would have been reported by a green run.

    Mutation: drop the EFFECT-006 block from _check_effects, or set its severity
    back to WARN.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "instructed.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    instructed_call_sites: 7\n"
        "    retry_contract: deduplicate", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    assert result.returncode != 0, result.stdout
    fails = [line.split()[1] for line in result.stdout.splitlines()
             if line.startswith("FAIL ")]
    assert "EFFECT-006" in fails, result.stdout
    assert "7" in result.stdout, result.stdout
    findings = json.loads((tmp_path / "findings.json").read_text())
    entries = [f for f in findings if f["rule"] == "EFFECT-006"]
    assert entries and all(f["severity"] == "FAIL" for f in entries), entries
    assert all(f["path"].endswith(".instructed_call_sites") for f in entries)


def test_zero_instructed_sites_is_not_a_finding(tmp_path):
    """The other rail, and the reason the field is written even when it is zero.

    A measured zero is the good case and has to read as the good case; if it
    warned, the derivation would emit a finding for every fully-scripted effect
    and the rule would be noise rather than a signal.

    Mutation: make the rule fire on `instructed is not None` rather than > 0.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "no-instructed.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    instructed_call_sites: 0\n"
        "    retry_contract: deduplicate", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    assert "EFFECT-006" not in result.stdout, result.stdout


def test_an_undecided_identity_still_reports_its_instructed_routes(tmp_path):
    """Two defects with different remedies, so two findings.

    EFFECT-006 carried a _declared(effect_identity) guard for one release, on
    the theory that an effect with no decided identity has a bigger problem and
    EFFECT-001 already says so. It does say so, and it does not say WHY: an
    author reading only EFFECT-001 goes and picks an identity, and four of the
    routes still cannot be checked afterwards. Overlapping effect findings are
    normal in this catalog; a silent second cause is not.

    Mutation: restore the _declared(effect_identity) guard on EFFECT-006.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "undecided-instructed.yaml"
    doc.write_text(clean.replace(
        "    retry_contract: deduplicate",
        "    instructed_call_sites: 4\n"
        "    retry_contract: deduplicate", 1).replace(
        "    effect_identity: notification_id",
        "    effect_identity: unknown", 1))
    result = run_cli("review", str(doc), "--out", str(tmp_path))
    assert "EFFECT-006" in result.stdout, result.stdout
    assert "EFFECT-001" in result.stdout, result.stdout


def _schema_properties(schema_path):

    """The effect object's property set, found by shape rather than by path.

    The two schemas nest the effect object differently, and hard-coding either
    path makes this test fail on a refactor that changed nothing real.
    """
    def find(node):
        if isinstance(node, dict):
            props = node.get("properties") or {}
            if node.get("type") == "object" and "effect_identity" in props:
                return props
            for value in node.values():
                found = find(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find(value)
                if found is not None:
                    return found
        return None

    found = find(json.loads(Path(schema_path).read_text()))
    assert found is not None, "no effect object in %s" % schema_path
    return found


def test_the_two_schemas_declare_the_same_effect_fields():
    """Both schemas set additionalProperties false, so a field declared in one
    and not the other is accepted in one document shape and REJECTED in the
    other -- for the same effect, written the same way.

    This is the general form of a gap a per-field test cannot cover: adding
    instructed_call_sites to only the factory schema left every test green,
    because nothing in the suite validates a standalone effect document against
    effect.schema.json. The next field would have gone the same way.

    Mutation: rename the property in either file.
    """
    standalone = _schema_properties(ROOT / "schemas" / "effect.schema.json")
    embedded = _schema_properties(ROOT / "schemas" / "factory.schema.json")
    assert set(standalone) == set(embedded), {
        "only in effect.schema.json": sorted(set(standalone) - set(embedded)),
        "only in factory.schema.json": sorted(set(embedded) - set(standalone)),
    }
    # The CONSTRAINTS too, not only the names. Equal name sets let one schema
    # declare instructed_call_sites as `integer` and the other as `number`:
    # both documents validate, the sets match, and a hand-written 0.5 then
    # passes the factory schema and slips past EFFECT-006, whose rule requires
    # an int. Descriptions are prose for a human and are allowed to differ.
    for field in sorted(standalone):
        left = {k: v for k, v in standalone[field].items() if k != "description"}
        right = {k: v for k, v in embedded[field].items() if k != "description"}
        assert left == right, (field, left, right)


def test_omitting_campaigns_scores_better_than_declaring_it(tmp_path):
    """The starter file and QUICKSTART both state this; here it is measured.

    `_check_campaigns` returns before emitting anything when the section is
    absent, so a contract that deletes campaigns gets a SHORTER findings list
    than one that declares a real completion rule. That is stated in the
    starter contract's header and in QUICKSTART, and a sentence about scoring
    behaviour that nothing runs is exactly the kind of claim this kit exists
    to catch.

    This test does not assert the behaviour is right -- it is not. It pins the
    documented numbers so that fixing the rule turns the DOCS red rather than
    leaving them quietly false, which is how the previous version of that
    paragraph ("an omitted section and a section that says unknown produce the
    same findings") survived.

    Mutation that flips it, measured: delete the `if campaigns is None:
    return` guard in src/rules.py so an absent section is checked like a
    declared one. Counts equalise and this goes red, pointing at the two
    files whose prose then needs rewriting.
    """
    out = run_cli("init", "--out", str(tmp_path))
    assert out.returncode == 0, out.stderr
    omitted = tmp_path / "factory.yaml"
    assert "campaigns:" not in omitted.read_text()

    declared = tmp_path / "with-campaigns.yaml"
    declared.write_text(
        omitted.read_text()
        + "\ncampaigns:\n  completion:\n"
          "    all_current_targets_have_disposition: [published]\n")

    def counts(path):
        res = run_cli("review", str(path), "--out", str(tmp_path / "out"))
        line = [ln for ln in res.stdout.splitlines()
                if re.match(r"^\d+ FAIL, \d+ WARN$", ln)]
        assert len(line) == 1, res.stdout
        fail, warn = re.match(r"^(\d+) FAIL, (\d+) WARN$", line[0]).groups()
        return int(fail), int(warn)

    assert counts(omitted) == (5, 8)
    assert counts(declared) == (5, 9)
    # The point, stated as a comparison so a change in the starter's other
    # sections cannot make this pass for the wrong reason.
    assert counts(omitted) < counts(declared)


# --- observability.objectives (2026-08-19) -------------------------------
#
# The schema carried `promises` and nothing else, so a contract could say it
# WATCHES a transition and never say what it watches it against. A promise with
# no threshold cannot be breached, which makes it a dashboard rather than a
# watch, and a watch that cannot go red is the defect this whole kit is about.
#
# The unit is the part that has to be enforced rather than documented. An
# objective is written once and re-read rarely; `p90: 7` silently meaning seven
# SECONDS instead of seven days would fire on every reading, and an alarm that
# fires constantly gets switched off. So the schema requires an explicit suffix
# and refuses a bare number.

def _with_objectives(tmp_path, block, name="obj.yaml"):
    """A schema-valid contract carrying one observability block."""
    import yaml
    doc = yaml.safe_load((EXAMPLES / "minimal-factory.yaml").read_text())
    doc["observability"] = block
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc))
    return path


def test_objectives_validate_alongside_promises(tmp_path):
    path = _with_objectives(tmp_path, {
        "promises": ["started_to_progress", "published_to_acknowledged"],
        "objectives": {"started_to_progress": {"p90": "4d"},
                       "published_to_acknowledged": {"p99": "36h"}},
    })
    result = run_cli("validate", str(path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_promises_without_objectives_still_validate(tmp_path):
    # Optional, deliberately. Making it required would invalidate every
    # contract written before this field existed, including the two examples
    # in this repo, and a schema break is a worse outcome than an undeclared
    # threshold. The gap is reported by the watch, not by the schema.
    path = _with_objectives(tmp_path, {"promises": ["started_to_progress"]})
    assert run_cli("validate", str(path)).returncode == 0


BAD_OBJECTIVES = {
    # A bare number: the unit would be a guess, and the guess is the bug.
    "bare_number": {"p90": 7},
    "bare_numeric_string": {"p90": "7"},
    # A unit spelled out is not one of s/m/h/d.
    "spelled_unit": {"p90": "36 hours"},
    "unknown_unit": {"p90": "36w"},
    # Two thresholds in one entry: they are two objectives and would be
    # reported as one, so whichever the reader's parser happened to pick would
    # be the one enforced.
    "two_percentiles": {"p90": "4d", "p99": "9d"},
    # No threshold at all.
    "empty": {},
    # A key that is not pNN.
    "not_a_percentile": {"ninety": "4d"},
    "out_of_range_low": {"p0": "4d"},
    "out_of_range_high": {"p101": "4d"},
    # A negative duration.
    "negative": {"p90": "-4d"},
}


@pytest.mark.parametrize("case", sorted(BAD_OBJECTIVES))
def test_a_malformed_objective_is_refused_by_the_schema(tmp_path, case):
    path = _with_objectives(tmp_path,
                            {"promises": ["started_to_progress"],
                             "objectives": {"started_to_progress": BAD_OBJECTIVES[case]}},
                            name=f"{case}.yaml")
    result = run_cli("validate", str(path))
    assert result.returncode != 0, (
        f"{case} validated; the schema accepts an objective it cannot enforce\n"
        + result.stdout + result.stderr)


def test_an_objective_block_that_is_not_a_mapping_is_refused(tmp_path):
    path = _with_objectives(tmp_path, {"promises": ["started_to_progress"],
                                       "objectives": ["p90: 4d"]})
    assert run_cli("validate", str(path)).returncode != 0


def test_an_unknown_key_under_observability_is_still_refused(tmp_path):
    # additionalProperties stays false. Adding `objectives` widened the schema
    # by exactly one key; the mutation this catches is widening it to anything,
    # which would silently accept a misspelled `objective` and enforce nothing.
    path = _with_objectives(tmp_path, {"promises": ["started_to_progress"],
                                       "objective": {"started_to_progress": {"p90": "4d"}}})
    assert run_cli("validate", str(path)).returncode != 0


# --- observability objectives as a REVIEW rule (2026-08-19) ----------------
#
# The schema landed `objectives` as optional and the review said nothing about
# it, so a contract listing all six canonical promises with zero objectives
# passed OBS-001 clean while watching nothing. A promise with no threshold
# cannot be breached, and a review that counts promises reads that as coverage.
#
# WARN, never FAIL, and the distinction is the point: an undeclared promise is
# a transition nobody is looking at, an undecided threshold is a transition
# somebody is looking at without having decided what bad means. Those are
# different pieces of work and want different urgency.
#
# The orphan rule is the same defect pointing the other way. The schema's own
# description already says every objectives key must appear in promises, and a
# JSON Schema cannot express a cross-field constraint, so it was documentation
# with nothing behind it. An objective left behind by a withdrawn promise is
# dead configuration that reads like coverage -- the more misleading of the two,
# because it looks MORE watched than the contract actually is.

CANONICAL_PROMISES = rules_mod.CANONICAL_PROMISES


def _obs_review(tmp_path, block, name="obs.yaml"):
    """Review a schema-valid contract carrying one observability block."""
    path = _with_objectives(tmp_path, block, name=name)
    return run_cli("review", str(path), "--out", str(tmp_path / "out"))


def _finding(out_dir, code):
    """The full finding record for one code, or None.

    The stdout lines carry the code and the severity only, so an assertion
    about WHICH promises a finding names has to read the machine-readable
    output -- which is also what a consumer of this kit reads.
    """
    import json
    data = json.loads((Path(out_dir) / "findings.json").read_text())
    records = data["findings"] if isinstance(data, dict) else data
    for record in records:
        if record.get("rule") == code:
            return record
    return None


def test_promises_with_no_objectives_warn(tmp_path):
    # The exact shape the rule exists for: full canonical coverage, nothing
    # watched. Before this rule the same contract produced no observability
    # finding at all.
    result = _obs_review(tmp_path, {"promises": list(CANONICAL_PROMISES)})
    warns = finding_lines(result.stdout, "WARN")
    assert any("OBS-002" in line for line in warns), result.stdout
    assert not any("OBS-001" in line for line in warns), \
        "all six are declared, so OBS-001 must not fire: " + result.stdout
    assert not any("OBS-0" in line for line in finding_lines(result.stdout, "FAIL")), \
        "an undecided threshold is not the same defect as an undeclared promise"


def test_objectives_on_every_promise_is_silent(tmp_path):
    result = _obs_review(tmp_path, {
        "promises": list(CANONICAL_PROMISES),
        "objectives": {p: {"p95": "1h"} for p in CANONICAL_PROMISES},
    })
    # Scoped to the observability codes: this fixture is built on
    # minimal-factory.yaml, which carries unrelated findings of its own.
    assert not any("OBS-0" in line for line in finding_lines(result.stdout, "WARN")), result.stdout
    assert not any("OBS-0" in line for line in finding_lines(result.stdout, "FAIL")), result.stdout


def test_partial_objectives_name_only_the_unwatched(tmp_path):
    # Naming them matters more than counting them: "2 promises lack objectives"
    # tells a reader to go diff two lists by hand.
    watched = list(CANONICAL_PROMISES)[:4]
    result = _obs_review(tmp_path, {
        "promises": list(CANONICAL_PROMISES),
        "objectives": {p: {"p95": "1h"} for p in watched},
    })
    record = _finding(tmp_path / "out", "OBS-002")
    assert record is not None, result.stdout
    detail = json.dumps(record)
    for p in CANONICAL_PROMISES:
        if p in watched:
            assert p not in detail, f"{p} has an objective and must not be named: {detail}"
        else:
            assert p in detail, f"{p} has no objective and must be named: {detail}"


def test_objective_for_an_undeclared_promise_warns(tmp_path):
    # Dead configuration that reads like coverage. Withdraw a promise, leave its
    # threshold behind, and the contract looks more watched than it is.
    result = _obs_review(tmp_path, {
        "promises": ["started_to_progress"],
        "objectives": {"started_to_progress": {"p95": "1h"},
                       "verified_to_published": {"p95": "1h"}},
    })
    assert any("OBS-003" in l for l in finding_lines(result.stdout, "WARN")), result.stdout
    record = _finding(tmp_path / "out", "OBS-003")
    detail = json.dumps(record)
    assert "verified_to_published" in detail, detail
    assert "started_to_progress" not in detail, \
        "a promise that IS declared must not be named as an orphan: " + detail


def test_no_observability_section_reports_only_obs_001(tmp_path):
    # OBS-001 already says nothing is watched. Adding "and none of the things
    # you did not declare have thresholds" is noise, and it is the shape that
    # makes people stop reading a findings list.
    minimal = EXAMPLES / "minimal-factory.yaml"
    result = run_cli("review", str(minimal), "--out", str(tmp_path / "out"))
    warns = finding_lines(result.stdout, "WARN")
    assert any("OBS-001" in line for line in warns), result.stdout
    assert not any("OBS-002" in line or "OBS-003" in line for line in warns), result.stdout


def test_reference_examples_declare_their_objectives(tmp_path):
    # The kit's own reference contracts have to model the shape the rule asks
    # for. This is the test that would have caught shipping the rule while
    # leaving our own examples tripping over it.
    # Derived from the pinned expectations rather than hand-listed: an example
    # added later would silently escape a hand-list, which is the failure mode
    # this kit spends most of its rules on.
    clean = [name for name, expected in EXPECTED_EXAMPLE_FINDINGS.items()
             if not expected["FAIL"] and not expected["WARN"]]
    assert clean, "no clean reference example is pinned; this test proves nothing"
    for name in clean:
        result = run_cli("review", str(_example_path(name)), "--out", str(tmp_path / name.replace("/", "-")))
        warns = finding_lines(result.stdout, "WARN")
        assert not any("OBS-00" in line for line in warns), f"{name}: {result.stdout}"


# examples/README.md enumerates the unsafe contract's findings twice over: once
# as a spelled-out count in a <summary>, and once as a table with one row per
# finding. Neither form is a digit, so both rails above -- which key on
# r"\d+ FAIL, \d+ WARN" -- are blind to them, and the page says in its own prose
# that "the counts are pinned by tests". They were not. Adding OBS-002 moved the
# real count to eleven and left the word "Ten" and a nine-row table standing,
# and the full suite stayed green; a cross-family reviewer found it by reading.
#
# So the third rail reads the page's tables and compares them to the live
# findings, with multiplicity, by SET EQUALITY. A row that should have gone away
# fails too, which is the half a subset check would miss.
_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six",
                 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven",
                 12: "Twelve", 13: "Thirteen", 14: "Fourteen", 15: "Fifteen",
                 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
                 19: "Nineteen", 20: "Twenty"}


def _details_blocks(text):
    """{summary text: [rule codes in the block's table rows]} for each <details>."""
    blocks, summary, rules_seen = {}, None, []
    for line in text.splitlines():
        match = re.search(r"<summary>(.*?)</summary>", line)
        if match:
            summary, rules_seen = match.group(1), []
            continue
        if "</details>" in line and summary is not None:
            blocks[summary] = rules_seen
            summary = None
            continue
        if summary is not None:
            cell = re.match(r"\|\s*`([A-Z]+-\d+)`\s*\|", line)
            if cell:
                rules_seen.append(cell.group(1))
    return blocks


def test_examples_readme_tables_enumerate_the_actual_findings(tmp_path):
    """The spelled-out counts and the per-rule tables both stay true."""
    result = run_cli("review", str(UNSAFE), "--out", str(tmp_path))
    findings = json.loads((tmp_path / "findings.json").read_text())
    blocks = _details_blocks((ROOT / "examples/README.md").read_text())
    assert blocks, "no <details> blocks found in examples/README.md; the parse is broken"

    for severity in ("FAIL", "WARN"):
        live = sorted(f["rule"] for f in findings if f["severity"] == severity)
        word = _NUMBER_WORDS[len(live)]
        label = f"{word} {'failure' if severity == 'FAIL' else 'warning'}"
        matching = [s for s in blocks if s.lower().startswith(label.lower())]
        assert matching, (
            f"examples/README.md has no <details> summary reading "
            f"{label + ('s' if len(live) != 1 else '')!r}; it reads "
            f"{sorted(blocks)!r} and the unsafe contract now produces "
            f"{len(live)} {severity} findings")
        assert sorted(blocks[matching[0]]) == live, (
            f"the {severity} table in examples/README.md lists "
            f"{sorted(blocks[matching[0]])} and the unsafe contract produces "
            f"{live}; add the missing row or drop the stale one")


# ---------------------------------------------------------------------------
# cites: the path:line references a contract makes about code
#
# Motivated by a real one. Our own contract carried
# internal/worker/runtime_handle.go:266-276 as a site that collapses a named
# UNKNOWN to SUCCESS; on re-derivation that function propagates, and the same
# file separately carried the CORRECTED version sixty lines away. Two
# statements about one function, in one document, one of them false, for a day.
# `reconcile` cannot see either: cites live in comments, which the YAML loader
# throws away.


def _minimal_contract_text():
    """The kit's own minimal contract, which carries no cites of its own.

    Verified rather than assumed: cites.extract() returns [] for it, so a case
    below asserting "no cites found" is measuring the fixture it wrote and not
    inheriting one.
    """
    return (EXAMPLES / "minimal-factory.yaml").read_text()


def _tree(root, files):
    """Write {relative path: line count or text} under root."""
    for relative, content in files.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, int):
            content = "\n".join(f"line {n}" for n in range(1, content + 1))
        target.write_text(content)
    return str(root)


def test_cites_extracts_from_comments_not_from_the_parsed_document():
    """A contract's cites are in comments; a parsed-tree walk would see none."""
    from src import cites as cites_mod
    text = ("# see internal/runtime/tmux/tmux.go:213\n"
            "effects:\n"
            "  - name: x  # adapter.go:1332-1341\n")
    found = cites_mod.extract(text)
    assert [c.raw for c in found] == ["internal/runtime/tmux/tmux.go:213",
                                      "adapter.go:1332-1341"]
    assert (found[1].start, found[1].end) == (1332, 1341)


def test_cites_does_not_match_urls_versions_or_yaml_keys():
    """The three shapes that look like a cite and are not.

    Each of these appears in real contracts. A checker that reported them would
    produce a page of findings on a healthy file, and the next person would
    stop running it -- which is the failure mode, not the false positives.
    """
    from src import cites as cites_mod
    text = ("# https://example.com:8080/path/to/thing.go\n"
            "  timeout: 30\n"
            "# pinned at v1.2.3 and gascity-main @b726b41f1\n")
    assert cites_mod.extract(text) == []


def test_cites_reports_a_file_that_is_not_there(tmp_path):
    root = _tree(tmp_path / "repo", {"internal/a.go": 50})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# gone: internal/deleted.go:12\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 1
    assert "MISSING" in result.stdout and "internal/deleted.go:12" in result.stdout


def test_cites_reports_a_line_past_the_end_of_the_file(tmp_path):
    """The exact shape our contract rotted into: the file survived, the line did not."""
    root = _tree(tmp_path / "repo", {"internal/a.go": 50})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# stale: internal/a.go:266-276\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 1
    assert "OUT OF RANGE" in result.stdout
    assert "file has 50 lines" in result.stdout


def test_cites_accepts_a_range_that_ends_on_the_last_line(tmp_path):
    """Off-by-one rail: the boundary is inclusive, so the last line resolves."""
    root = _tree(tmp_path / "repo", {"internal/a.go": 50})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# ok: internal/a.go:40-50\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0, result.stdout
    assert "1 resolved" in result.stdout and "0 broken" in result.stdout


def test_cites_resolves_a_bare_filename_and_flags_an_ambiguous_one(tmp_path):
    """Bare filenames are how contracts are actually written, and they collide."""
    # NOT vendor/ or a nested checkout: those are skipped by design and would
    # make this pass for the wrong reason. Two ordinary copies is the case.
    root = _tree(tmp_path / "repo", {"internal/tmux.go": 50, "cmd/only.go": 50,
                                     "pkg/tmux.go": 50})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text()
                        + "\n# unique: only.go:10\n# collides: tmux.go:10\n")
    result = run_cli("cites", str(contract), root)
    # Ambiguity is reported and does not fail: the contract is not wrong, the
    # check cannot tell which file was meant, and guessing would make a green
    # run mean less than it says.
    assert result.returncode == 0, result.stdout
    assert "AMBIGUOUS" in result.stdout and "tmux.go:10" in result.stdout
    assert "1 resolved" in result.stdout and "1 ambiguous" in result.stdout


def test_cites_spans_multiple_roots(tmp_path):
    """A contract cites more than one repository; ours cites two."""
    city = _tree(tmp_path / "city", {"bin/thing.sh": 20})
    source = _tree(tmp_path / "source", {"internal/runtime/tmux/tmux.go": 3000})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text()
                        + "\n# bin/thing.sh:5 and internal/runtime/tmux/tmux.go:2208-2223\n")
    result = run_cli("cites", str(contract), city, source)
    assert result.returncode == 0, result.stdout
    assert "2 resolved" in result.stdout and "0 broken" in result.stdout


def test_cites_refuses_a_root_that_does_not_exist(tmp_path):
    """A typo'd root would make every cite report missing.

    Exit 2, not 1: a wall of findings caused by a bad argument is
    indistinguishable from a contract that rotted through, and the second is
    the one someone would act on.
    """
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# internal/a.go:12\n")
    result = run_cli("cites", str(contract), str(tmp_path / "nope"))
    assert result.returncode == 2
    assert "not a directory" in result.stdout
    assert "MISSING" not in result.stdout


def test_cites_says_what_a_green_run_does_not_prove(tmp_path):
    """The limit is printed, not implied.

    A check that let "resolved" read as "the contract's claim was confirmed"
    would be worse than no check: the whole reason cites rot is that the line
    keeps existing while its meaning moves.
    """
    root = _tree(tmp_path / "repo", {"internal/a.go": 50})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# internal/a.go:12\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0
    assert "does NOT mean the line still says" in result.stdout.replace("\n", " ")


def test_cites_on_a_contract_with_no_cites_says_so(tmp_path):
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text())
    root = _tree(tmp_path / "repo", {"internal/a.go": 50})
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0
    assert "no path:line cites found" in result.stdout


def test_cites_ignores_a_line_number_inside_a_url():
    """A link to a hosted file is not a claim about the local tree.

    This case exists because the first guard could not catch it. The path
    pattern allows a leading `/`, so the match began INSIDE the scheme
    (`https:` + `/github.com/...`), and every "is the text before this a URL"
    test saw `https:/` and passed it through -- reporting a mangled path that
    could never resolve. URLs are now blanked before the scan instead.
    """
    from src import cites as cites_mod
    line = "see https://github.com/org/repo/blob/main/internal/a.go:12 here"
    assert cites_mod.extract(line) == []


def test_cites_reads_an_absolute_path(tmp_path):
    """An absolute cite is a real thing people write, and resolves on its own.

    Note what this means: an absolute path answers from the filesystem, not
    from the roots given on the command line. That is the honest behaviour --
    the cite named a location, not a location relative to something -- but it
    is worth knowing before reading a green run as "everything resolved under
    the roots I passed".
    """
    from src import cites as cites_mod
    target = tmp_path / "abs" / "handler.go"
    target.parent.mkdir(parents=True)
    target.write_text("\n".join(f"line {n}" for n in range(1, 100)))
    found = cites_mod.resolve(
        cites_mod.extract(f"# boom at {target}:99\n"), [str(tmp_path)])
    assert [(c.status, c.start) for c in found] == [("resolved", 99)]


def test_cites_stitches_a_path_wrapped_across_two_comment_lines():
    """Half the first version's findings on our own contract were this bug.

    An author wrapping at the column limit writes `assets/scripts/` on one line
    and `gascity-ship-gate.sh:198-210` on the next. Reported as a broken cite,
    that is a false positive naming a file the contract never claimed -- and a
    checker whose findings are half its own artifacts stops being run.
    """
    from src import cites as cites_mod
    text = ("  # refuses to push when it differs from the SHA (assets/scripts/\n"
            "  # gascity-ship-gate.sh:198-210). That is a correct check.\n")
    found = cites_mod.extract(text)
    assert [c.raw for c in found] == ["assets/scripts/gascity-ship-gate.sh:198-210"]
    # The line reported is where the cite starts, which is where a reader looks.
    assert found[0].line_number == 1


def test_cites_does_not_stitch_a_yaml_value_that_ends_in_a_slash():
    """The rejoin is narrow on purpose: a comment continued by a comment.

    Both halves are checked, because they are separate guards and the first
    version of this test only exercised one of them: widening the pattern that
    decides a line is CONTINUED left every case green, since the follow-line
    check caught them anyway. A directory value happening to precede a comment
    is the input that tells the two apart, and it stitches a path the contract
    never wrote.
    """
    from src import cites as cites_mod
    # (a) the continued line is not a comment; the follow line is.
    stitched = "workdir: /var/tmp/\n# handler.go:99\n"
    assert [c.raw for c in cites_mod.extract(stitched)] == ["handler.go:99"]
    # (b) the continued line is a comment; the follow line is not.
    text = ("# see internal/\n"
            "note: handler.go:99\n")
    assert [c.raw for c in cites_mod.extract(text)] == ["handler.go:99"]


def test_cites_skips_a_nested_checkout_and_a_vendor_copy(tmp_path):
    """A copy of code that lives elsewhere is not a second candidate.

    Measured on our own installation before this: a stray gascity checkout and
    a bead worktree, both sitting inside the city root, made seven bare cites
    ambiguous against their own duplicates. That is a fact about the disk, not
    about the contract, and it buried the four real findings under twenty-four.
    """
    root = tmp_path / "repo"
    _tree(root, {"cmd/a.go": 50,
                 "worktrees/copy/cmd/a.go": 50,
                 "vendor/pkg/a.go": 50})
    (root / "worktrees" / "copy" / ".git").write_text("gitdir: elsewhere\n")
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# a.go:10\n")
    result = run_cli("cites", str(contract), str(root))
    assert result.returncode == 0, result.stdout
    assert "AMBIGUOUS" not in result.stdout
    assert "1 resolved" in result.stdout


def test_cites_uses_a_full_path_the_contract_gave_earlier(tmp_path):
    """The short form is resolved from the document's own earlier text.

    Counted separately from a plain resolve, because it IS an inference: the
    contract said `internal/x/tmux.go` once and `tmux.go` four times, and the
    second reading rests on the first rather than on anything written at the
    cite itself.
    """
    root = _tree(tmp_path / "repo", {"internal/x/tmux.go": 3000,
                                     "other/y/tmux.go": 3000})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text()
                        + "\n# internal/x/tmux.go:213 then later tmux.go:2208-2223\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0, result.stdout
    assert "AMBIGUOUS" not in result.stdout
    assert "1 resolved, 1 resolved via a full path" in result.stdout


def test_cites_will_not_guess_when_the_contract_pinned_two_full_paths(tmp_path):
    """The inference stops exactly where it would have to choose.

    A basename the contract itself wrote out as two different full paths is
    genuinely ambiguous in the short form, and resolving it would put a guess
    behind a green run.
    """
    root = _tree(tmp_path / "repo", {"internal/x/tmux.go": 3000,
                                     "other/y/tmux.go": 3000})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text()
                        + "\n# internal/x/tmux.go:213 and other/y/tmux.go:9"
                          " then bare tmux.go:2208\n")
    result = run_cli("cites", str(contract), root)
    assert "AMBIGUOUS" in result.stdout and "tmux.go:2208" in result.stdout
    assert "2 resolved" in result.stdout and "1 ambiguous" in result.stdout


def test_contract_reference_documents_every_subcommand():
    """The command table stays the parser's, in both directions.

    It had drifted three commands behind before this rail existed -- `infer`,
    `probes-init` and `reconcile` were live, documented in README prose, and
    absent from the table that calls itself Commands. A reader deciding what
    the tool can do reads the table.
    """
    source = (ROOT / "src" / "factory_check.py").read_text()
    live = set(re.findall(r'sub\.add_parser\(\s*"([a-z-]+)"', source))
    assert live, "no subparsers found; the parse is broken, not the docs"
    reference = (ROOT / "docs" / "contract-reference.md").read_text()
    table = re.findall(r"factory_check\.py ([a-z-]+)", reference)
    assert live - set(table) == set(), (
        "subcommands with no row in docs/contract-reference.md: "
        f"{sorted(live - set(table))}")
    assert set(table) - live == set(), (
        "docs/contract-reference.md names subcommands the CLI does not have: "
        f"{sorted(set(table) - live)}")


def test_cites_prefers_a_file_sitting_at_the_root_over_deeper_copies(tmp_path):
    """A bare name that IS a root-relative path resolves to the root's file.

    The full-path pass cannot help here: for a file at the root, the path from
    the root and the bare name are the same string, so a contract that writes
    `city.toml` gives the disambiguating pass nothing to work with. Measured on
    our own installation, two of the three remaining ambiguities were this.
    """
    root = _tree(tmp_path / "repo", {"city.toml": 300,
                                     "fixtures/a/city.toml": 5,
                                     "fixtures/b/city.toml": 5})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# city.toml:271\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0, result.stdout
    assert "AMBIGUOUS" not in result.stdout
    # 271 is inside the root copy and past the end of both fixture copies, so
    # a green run here also proves it picked the right one rather than merely
    # picking one.
    assert "1 resolved" in result.stdout and "0 broken" in result.stdout


def test_cites_still_reports_two_copies_that_are_both_below_the_root(tmp_path):
    """The preference is for a file AT a root, not for the shallowest one."""
    root = _tree(tmp_path / "repo", {"internal/store.go": 90, "pkg/store.go": 90})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# store.go:47-48\n")
    result = run_cli("cites", str(contract), root)
    assert "AMBIGUOUS" in result.stdout and "1 ambiguous" in result.stdout


def test_cites_terminates_when_a_wrap_ends_the_comment_block():
    """A path cut at the last line of a block must not rejoin forever."""
    from src import cites as cites_mod
    # Trailing slash with nothing to continue it, then a bare `#`, then the
    # same at the very end of the text. Each was an infinite loop.
    text = "# see assets/scripts/\n#\n# handler.go:99\n# internal/\n"
    found = cites_mod.extract(text)
    assert [c.raw for c in found] == ["handler.go:99"]


def test_cites_stitches_a_path_cut_at_a_hyphen_a_dot_and_a_colon():
    """Our own contract wraps at four different characters in one paragraph.

    Three of the five cites in it were invisible to the first version, which
    only handled a trailing slash: a cite the tool never saw reads exactly
    like one it checked and found fine.
    """
    from src import cites as cites_mod
    text = ("    # three sites push by name only -- mol-pr-from-issue.formula.\n"
            "    # toml:711, .beads/formulas/mol-polecat-\n"
            "    # commit.toml:118. One is conditional: mol-adopt-pr.toml:\n"
            "    # 717-719 chooses --force-with-lease.\n")
    assert [c.raw for c in cites_mod.extract(text)] == [
        "mol-pr-from-issue.formula.toml:711",
        ".beads/formulas/mol-polecat-commit.toml:118",
        "mol-adopt-pr.toml:717-719",
    ]


def test_cites_does_not_stitch_an_ordinary_sentence_ending_in_a_period():
    """The period case is the one that would run away if left uncorroborated."""
    from src import cites as cites_mod
    text = ("# measured rather than assumed.\n"
            "# Corrected here as well, in handler.go:99.\n")
    found = cites_mod.extract(text)
    assert [c.raw for c in found] == ["handler.go:99"]
    assert found[0].line_number == 2, "attribution moved to the wrong line"


def test_cites_resolves_a_partial_path_from_the_end(tmp_path):
    """`issueops/lease.go` for a file at `internal/storage/issueops/lease.go`.

    Our own contract cites the beads repository this way. Before this, passing
    that repository as a root changed nothing -- the cite was still MISSING,
    which reads as a deleted file rather than as an abbreviated path.
    """
    root = _tree(tmp_path / "repo", {"internal/storage/issueops/lease.go": 200,
                                     "cmd/main.go": 10})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# issueops/lease.go:99-115\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0, result.stdout
    assert "1 resolved" in result.stdout and "0 broken" in result.stdout


def test_cites_will_not_take_a_partial_path_that_matches_two_files(tmp_path):
    """The suffix fallback gets the same ambiguity discipline as a bare name."""
    root = _tree(tmp_path / "repo", {"a/issueops/lease.go": 200,
                                     "b/issueops/lease.go": 200})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# issueops/lease.go:99\n")
    result = run_cli("cites", str(contract), root)
    assert "AMBIGUOUS" in result.stdout and "1 ambiguous" in result.stdout


def test_cites_does_not_let_a_partial_path_match_a_longer_name(tmp_path):
    """`ops/lease.go` must not match `.../issueops/lease.go`.

    The suffix is anchored at a path separator. Matching on the raw string
    would resolve a directory the contract never named.
    """
    root = _tree(tmp_path / "repo", {"internal/issueops/lease.go": 200})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# ops/lease.go:99\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 1
    assert "MISSING" in result.stdout and "ops/lease.go:99" in result.stdout


def test_cites_blanks_an_uppercase_url_scheme_too():
    """`HTTPS://host/path/x.go:77` fabricated a path, it did not miss one.

    The lowercase-only guard let the scheme's own slashes start a match, so the
    reported cite was `/example.com/path/internal.go:77` -- a path no contract
    ever wrote. Cross-family review, 2026-08-19.
    """
    from src import cites as cites_mod
    assert cites_mod.extract("# See HTTPS://example.com/path/internal.go:77\n") == []
    assert cites_mod.extract("# See Https://example.com/a/b.go:77\n") == []
    # The lowercase case keeps working, and a real cite beside a URL survives.
    found = cites_mod.extract("# https://example.com/x see internal/a.go:12\n")
    assert [c.raw for c in found] == ["internal/a.go:12"]


def test_cites_stitches_a_single_hyphen_name_when_the_next_line_corroborates():
    """`api-` + `service.go:120` is a real wrap the token cannot show alone."""
    from src import cites as cites_mod
    assert [c.raw for c in cites_mod.extract("# api-\n# service.go:120\n")] == [
        "api-service.go:120"]
    # Prose hyphenation does not stitch: the next line is not a filename and a
    # line number, which is the entire corroboration.
    assert [c.raw for c in cites_mod.extract("# well-\n# known in handler.go:9\n")] == [
        "handler.go:9"]


def test_cites_says_what_it_never_searched_when_a_file_is_not_found(tmp_path):
    """"no such file under any root" was true and read as "deleted".

    The indexer skips dot-directories, symlinks, vendored copies and nested
    checkouts on purpose. A reader who knows the file exists needs the message
    to name that, or the tool looks broken.
    """
    root = _tree(tmp_path / "repo", {".internal/worker.go": 40, "cmd/main.go": 5})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text() + "\n# worker.go:10\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 1
    assert "dot-directories" in result.stdout and "nested checkouts" in result.stdout


def test_cites_names_the_file_each_inference_chose(tmp_path):
    """An inference the reader cannot see is one they cannot disagree with.

    A basename the contract pinned to one full path elsewhere still resolves to
    that path when a different file of the same name was meant. Counting those
    separately was the whole mitigation and it was invisible in the output.
    """
    root = _tree(tmp_path / "repo", {"first/main.go": 30, "second/main.go": 30})
    contract = tmp_path / "factory.yaml"
    contract.write_text(_minimal_contract_text()
                        + "\n# first/main.go:5 and later main.go:20\n")
    result = run_cli("cites", str(contract), root)
    assert result.returncode == 0, result.stdout
    assert "INFERRED" in result.stdout
    assert "first/main.go" in result.stdout and "main.go:20" in result.stdout


def _lanes_install_with_a_mention(tmp_path):
    """The lanes installation, plus one line that names the verb without calling it.

    A shell diagnostic that names the command it wraps is the normal shape of a
    fenced wrapper, not an edge case, so this is what a real installation looks
    like rather than a contrived input.
    """
    contract, probes, root = _lanes_install(tmp_path)
    (root / "bin" / "publish").write_text(
        "#!/usr/bin/env bash\n"
        "slack_post --idempotency-key \"$KEY\" \"$MSG\"\n"
        "echo \"warn: slack_post failed; nothing was sent\" >&2\n")
    return contract, probes, root


def test_reconcile_names_the_matches_it_tells_you_to_exclude(tmp_path):
    """The instruction has to be followable from the output that gives it.

    Reconcile's reason string ends "exclude the ones that are not invocations
    with not_regex in the probe pack" and used to name none of them. Derive
    printed the paths; reconcile is the command run on a schedule and the one
    whose exit code gates, so a reader who only ever sees reconcile was told to
    go edit something and not told what. The locations alone are not enough
    either: the reader's job is to decide whether each line is an invocation,
    which needs the matched text.

    Mutation: delete the _print_review_lines call under the DRIFT loop in
    cmd_reconcile. The reason string still says "1 match(es) set aside" and
    every other assertion in this file still passes.
    """
    contract, probes, root = _lanes_install_with_a_mention(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    drift = [l for l in out.stdout.splitlines() if l.startswith("DRIFT  slack_publish")]
    assert drift, out.stdout
    assert "1 match(es) set aside for review" in drift[0]
    review = [l for l in out.stdout.splitlines() if "review (" in l]
    assert len(review) == 1, out.stdout
    assert "bin/publish:3" in review[0]
    assert "inside a quoted string" in review[0]
    # The matched text, because the location cannot tell the reader whether the
    # line is a call.
    assert "warn: slack_post failed" in review[0]


def test_reconcile_prints_no_review_block_when_nothing_was_set_aside(tmp_path):
    """The other rail. A clean installation must not grow a heading with
    nothing under it, or the block stops being a signal."""
    contract, probes, root = _lanes_install(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    assert not [l for l in out.stdout.splitlines() if "review (" in l], out.stdout


OPEN_CONTRACT = """\
version: factory.reliability/v1
factory:
  name: lanes
effects:
  - name: slack_publish
    destination: messaging
    # The schema requires the field, so "undecided" is spelled, not omitted --
    # which is the only way an effect reaches OPEN.
    effect_identity: unknown
    retry_contract: deduplicate
    unknown_state_policy: block_and_escalate
"""


def test_reconcile_names_the_set_aside_matches_on_an_open_effect_too(tmp_path):
    """OPEN is the other verdict a set-aside match can reach, and it is the
    worse one to leave bare.

    An effect nobody has decided yet, whose call sites include a line the
    scanner could not read as an invocation, is precisely the case where the
    reader is about to WRITE the contract entry. Telling them a match was set
    aside and not which one sends them to write a claim over evidence they
    cannot see.

    Mutation: delete the _print_review_lines call under the OPEN loop. Every other
    test in this file stays green, including both rails of the DRIFT one.
    """
    contract, probes, root = _lanes_install_with_a_mention(tmp_path)
    contract.write_text(OPEN_CONTRACT)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines() if l.startswith("OPEN  slack_publish")]
    assert line, out.stdout
    assert "1 match(es) set aside for review" in line[0]
    review = [l for l in out.stdout.splitlines() if "review (" in l]
    assert len(review) == 1, out.stdout
    assert "bin/publish:3" in review[0]
    assert "warn: slack_post failed" in review[0]


# The contract for the lanes install with merge_pull_request's count declared,
# so the STALE rail is live and a test can prove what does and does not move it.
THREE_SHAPES_CONTRACT = """\
version: factory.reliability/v1
factory:
  name: lanes
effects:
  - name: slack_publish
    destination: messaging
    effect_identity: idempotency_key
    retry_contract: deduplicate
    unknown_state_policy: block_and_escalate
  - name: merge_pull_request
    destination: code_host
    effect_identity: pr_head_sha
    retry_contract: converge
    unknown_state_policy: block_and_escalate
    instructed_call_sites: 3
"""

# The three shapes an instruction-lane match comes in, one line each, in a file
# that carries nothing else. Real prompt files mix them; separating them is what
# lets a count assertion mean something.
THREE_SHAPES = (
    'prompt = """\n'
    'Run gh pr merge once the checks are green.\n'
    'echo "gh pr merge is banned on this path"\n'
    'gh pr merge "$PR" --match-head-commit "$SHA"\n'
    '"""\n')


def _three_instruction_shapes(tmp_path, formula=THREE_SHAPES):
    """The lanes install whose instruction lane holds a plain direction, a
    prohibition the scanner cannot read as a call, and a literal command that
    spells the identity marker out.

    merge_pull_request has no scripted site here, so it reaches UNVERIFIED --
    the verdict that printed nothing at all before, and the one an agent-driven
    factory hits most.
    """
    contract, probes, root = _lanes_install(tmp_path)
    contract.write_text(THREE_SHAPES_CONTRACT)
    (root / "formulas" / "ship.toml").write_text(formula)
    return contract, probes, root


def test_reconcile_names_the_instruction_sites_behind_an_unverified_verdict(tmp_path):
    """The count EFFECT-006 fails on has to be readable as lines.

    UNVERIFIED means every route to the effect is prose, so the instructed count
    IS the whole finding, and it was the one verdict that printed no locations of
    any kind. The remedy the tool offers for a miscounted lane is a not_regex in
    the probe pack, and nobody can write one against a number.

    Mutation: delete the _print_review_lines call under the `unverifiable` loop
    in cmd_reconcile. The UNVERIFIED line still reports "all 3 call site(s) are
    agent instructions" and every other test in this file stays green.
    """
    contract, probes, root = _three_instruction_shapes(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    line = [l for l in out.stdout.splitlines()
            if l.startswith("UNVERIFIED  merge_pull_request")]
    assert line, out.stdout
    assert "all 3 call site(s) are agent instructions" in line[0]

    plain = [l for l in out.stdout.splitlines() if "instruction: " in l]
    assert len(plain) == 1, out.stdout
    assert "formulas/ship.toml:2" in plain[0]
    # The matched text, for the same reason the scripted lane carries it: the
    # reader is deciding whether this line is a direction to act, and a path
    # cannot tell them.
    assert "Run gh pr merge once the checks are green." in plain[0]


def test_reconcile_separates_a_prohibition_it_could_not_read_from_a_direction(tmp_path):
    """A line naming the verb in order to forbid it still counts, and says so.

    This is the dominant shape in the lane: prompts and work templates that name
    a command to ban it. The count cannot drop them -- a rule that did would be a
    guard that can only ever improve a score, and "never run the bare push, use
    the wrapper" both forbids and instructs, so no pattern decides it. What the
    reader gets instead is the line, its reason, and its text.

    Mutation: change instructed_unclassified to return [] in infer.py. The
    UNVERIFIED count stays 3, EFFECT-006 still fails, and only this assertion
    moves -- which is the point being fixed, stated as a test.
    """
    contract, probes, root = _three_instruction_shapes(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    review = [l for l in out.stdout.splitlines() if "review, instruction (" in l]
    assert len(review) == 1, out.stdout
    assert "inside a quoted string" in review[0]
    assert "formulas/ship.toml:3" in review[0]
    assert "banned on this path" in review[0]


def test_reconcile_reports_an_instruction_that_spells_the_marker_out(tmp_path):
    """A work template writing the whole command out is evidence, and it is
    weaker evidence than code.

    The field's own schema text says a nonzero count is "NOT a claim that they
    omit it", and this is the case where we can see that it does not. It is
    reported and deliberately not subtracted: a literal command in a prompt is a
    template an agent may edit before running, so letting it reduce the residual
    would improve a score on the strength of prose. The count assertion below is
    that non-subtraction.

    Mutation: return `s.has_identity` sites from instructed_plain as well. The
    site is then printed twice, the count is unchanged, and this test catches it.
    """
    contract, probes, root = _three_instruction_shapes(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    spelled = [l for l in out.stdout.splitlines() if "instruction spells out " in l]
    assert len(spelled) == 1, out.stdout
    assert "--match-head-commit" in spelled[0]
    assert "formulas/ship.toml:4" in spelled[0]
    # Not subtracted: the declared 3 still matches the scan, so no STALE.
    assert not [l for l in out.stdout.splitlines()
                if l.startswith("STALE") and "merge_pull_request" in l], out.stdout


def test_the_instruction_count_does_not_move_when_a_site_becomes_set_aside(tmp_path):
    """The mutation that proves the score cannot be improved by the new reporting.

    Three installations, identical except for how the middle line is written: as
    a prohibition inside quotes (set aside), as a plain sentence (readable), and
    with the identity marker spelled out. The declared 3 holds against all three,
    so no reordering of a line between the three groups can quiet a finding. That
    is the property that makes this change reporting-only, and it is the one that
    would break first if a later "clean this up" patch taught a group to subtract.
    """
    variants = {
        "set aside": THREE_SHAPES,
        "plain": THREE_SHAPES.replace(
            'echo "gh pr merge is banned on this path"',
            'Do not gh pr merge on this path.'),
        "marked": THREE_SHAPES.replace(
            'echo "gh pr merge is banned on this path"',
            'gh pr merge --match-head-commit "$OTHER"'),
    }
    for label, formula in variants.items():
        root_dir = tmp_path / label.replace(" ", "_")
        root_dir.mkdir()
        contract, probes, root = _three_instruction_shapes(root_dir, formula)
        out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
        assert not [l for l in out.stdout.splitlines() if l.startswith("STALE")], \
            "%s: %s" % (label, out.stdout)
        line = [l for l in out.stdout.splitlines()
                if l.startswith("UNVERIFIED  merge_pull_request")]
        assert line, "%s: %s" % (label, out.stdout)
        assert "all 3 call site(s) are agent instructions" in line[0], label


def test_reconcile_prints_no_instruction_lines_when_the_lane_is_empty(tmp_path):
    """The other rail. An effect performed only in code must not grow a group
    heading with nothing under it, or the block stops being a signal.

    slack_publish here has a scripted site and no instructed one, and it is in
    the same run as merge_pull_request, whose three lines do print -- so this
    asserts the absence is a property of the effect and not of the run.
    """
    contract, probes, root = _three_instruction_shapes(tmp_path)
    out = run_cli("reconcile", str(contract), str(root), "--probes", str(probes))
    lines = out.stdout.splitlines()
    slack = [i for i, l in enumerate(lines) if l.startswith("CONFIRMED  slack_publish")]
    assert slack, out.stdout
    following = []
    for l in lines[slack[0] + 1:]:
        if not l.startswith("         "):
            break
        following.append(l)
    assert not [l for l in following if "instruction" in l], following


def _effect_001_messages(tmp_path):
    """The EFFECT-001 messages alone, read from findings.json.

    The three tests below assert on message wording, and asserting a fragment
    against the whole of stdout lets any other finding satisfy them. codex-review
    raised it on the round that landed this change: the fragments are unique to
    EFFECT-001 in rules.py today, so the tests were green for the right reason,
    and nothing was keeping them that way. Pinning to the rule closes it before
    it is a debugging session.
    """
    findings = json.loads((tmp_path / "findings.json").read_text())
    return [f["message"] for f in findings if f["rule"] == "EFFECT-001"]

def test_an_undecided_identity_does_not_claim_the_destination_cannot_dedupe(tmp_path):
    """The finding may describe the contract; it may not describe the city.

    EFFECT-001 read one field and reported a conclusion about the destination:
    "the destination cannot recognize a repeat". On a contract straight out of
    infer, every effect is undecided by construction, so it fired once per
    effect -- including on effects whose every code call site carries the
    idempotency marker, in a file where the derived comment two lines above said
    so. The reader who checks one call site catches the tool being wrong about
    their own installation on the first run they ever do.

    Mutation: put the old clause back on the EFFECT-001 message.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "no-false-claim.yaml"
    doc.write_text(clean.replace(
        "    effect_identity: notification_id",
        "    effect_identity: unknown", 1))
    run_cli("review", str(doc), "--out", str(tmp_path))
    messages = _effect_001_messages(tmp_path)
    assert len(messages) == 1, messages
    assert "cannot recognize a repeat" not in messages[0], messages[0]
    assert "nothing in this contract establishes" in messages[0], messages[0]


def test_an_undecided_identity_names_the_code_lane_the_scan_did_observe(tmp_path):
    """Hand the operator the value, not a scolding.

    The observation is already in the contract -- infer writes code_lane_identity
    beside the undecided effect_identity precisely so the two answers can differ
    -- and until now no rule read it, so the one finding an author acts on
    withheld the one fact that tells them what to write. It is reported as an
    observation and never as the guarantee: a route that is prose carries
    whatever the agent types, which is why it did not simply become the answer.

    Mutation: drop the code_lane_identity clause from EFFECT-001.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "lane-observed.yaml"
    doc.write_text(clean.replace(
        "    effect_identity: notification_id",
        "    effect_identity: unknown\n"
        "    code_lane_identity: notification_id", 1))
    run_cli("review", str(doc), "--out", str(tmp_path))
    messages = _effect_001_messages(tmp_path)
    assert len(messages) == 1, messages
    assert "code lane carrying notification_id" in messages[0], messages[0]
    assert "not a declared guarantee" in messages[0], messages[0]


def test_an_undecided_code_lane_adds_no_clause(tmp_path):
    """`unknown` in the observation field is not an observation.

    _declared() is what keeps this honest: a derived contract writes
    code_lane_identity: unknown whenever the scan could not settle it, and a
    clause reading "the scan observed the code lane carrying unknown" would be
    worse than silence. The field being present is not the same as it saying
    something.

    Mutation: test the clause on presence (`is not None`) instead of _declared.
    """
    clean = ISSUE_TO_PR.read_text()
    doc = tmp_path / "lane-unknown.yaml"
    doc.write_text(clean.replace(
        "    effect_identity: notification_id",
        "    effect_identity: unknown\n"
        "    code_lane_identity: unknown", 1))
    run_cli("review", str(doc), "--out", str(tmp_path))
    messages = _effect_001_messages(tmp_path)
    assert len(messages) == 1, messages
    assert "the code lane carrying" not in messages[0], messages[0]


def _install_with_a_record_directory(tmp_path):
    """A factory whose own written record mentions the command it records.

    `gates/` is the shape that motivated this: 275 completed checklists on a
    real repository, each with a table row saying a `git status` check had
    PASSED, quoting the command. `docs/` is an agent instruction, which is the
    same file format and must NOT be excluded, so the fixture holds both.
    """
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "gates").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "bin" / "ship").write_text(
        "#!/usr/bin/env bash\ngit push origin main\n")
    for i in range(6):
        (root / "gates" / ("gate-%d.md" % i)).write_text(
            "| 5 | Branch clean | PASS | Before writing this gate, `git push "
            "origin main` had run |\n")
    (root / "docs" / "AGENTS.md").write_text(
        "Work is not complete until `git push origin main` succeeds.\n")
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    return root


_PROSE_BLOCK = "Every match in these is prose"


def _all_prose_names(stdout):
    """The directory names inside the all-prose block, and nothing else.

    Splitting on the marker and reading the tail was the first version, and
    codex-review was right that it is a false green: with the block removed,
    split returns the WHOLE output, whose histogram lists every directory by
    name, so the assertions still pass. The mutation that was supposed to prove
    the naming test broke a different test instead, and the difference was
    invisible from the pass/fail counts alone.
    """
    assert _PROSE_BLOCK in stdout, stdout
    line = stdout.split(_PROSE_BLOCK)[1].splitlines()[1]
    return {name.strip() for name in line.split(",")}

def test_probes_init_names_the_directories_whose_every_match_is_prose(tmp_path):
    """Otherwise the advice to exclude output directories cannot be taken.

    The report said "re-run with --exclude <dir> for each that is output" over a
    flat list of counts. On a real foreign repository the top contributor was
    `release-gates` at 37 hits, every one a table row in a completed checklist
    recording that a check had passed, and nothing on the screen distinguished
    it from `scripts` at 41. A name that reads as machinery, a number that reads
    as heavy use, and no signal at all.

    Mutation: drop the all-prose block from cmd_probes_init.
    """
    root = _install_with_a_record_directory(tmp_path)
    out = run_cli("probes-init", str(root), "--write", str(tmp_path / "p.yaml"))
    assert out.returncode == 0, out.stdout + out.stderr
    assert _all_prose_names(out.stdout) == {"gates", "docs"}, out.stdout


def test_probes_init_does_not_name_a_directory_that_holds_executed_matches(tmp_path):
    """The rail that makes the list mean something.

    A tool that listed every directory would be telling the reader to exclude
    the scripts that perform the effect, which is the one exclusion that makes
    the score wrong in the flattering direction. `bin/` holds the only executed
    call site in the fixture and must never appear.

    Mutation: count a path as prose unconditionally in directory_distribution.
    """
    root = _install_with_a_record_directory(tmp_path)
    out = run_cli("probes-init", str(root), "--write", str(tmp_path / "p.yaml"))
    assert "bin" not in _all_prose_names(out.stdout), out.stdout


def test_probes_init_says_nothing_when_no_directory_is_all_prose(tmp_path):
    """No finding, no paragraph.

    The block is advice about specific directories; printed over an empty list
    it becomes a standing warning the reader learns to skip, which is how a
    real signal gets ignored later.

    Mutation: print the block unconditionally.
    """
    root = _tiny_git_install(tmp_path)
    out = run_cli("probes-init", str(root), "--write", str(tmp_path / "p.yaml"))
    assert _PROSE_BLOCK not in out.stdout, out.stdout
