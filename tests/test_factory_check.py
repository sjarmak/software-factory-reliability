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
