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


def test_review_issue_to_pr_has_no_fail(tmp_path):
    result = run_cli("review", str(ISSUE_TO_PR), "--out", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert finding_lines(result.stdout, "FAIL") == []
    assert "RECON-001" in finding_rules(result.stdout, "WARN")


def test_review_strict_fails_on_warn(tmp_path):
    result = run_cli("review", str(ISSUE_TO_PR), "--strict", "--out", str(tmp_path))
    assert result.returncode == 1


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
