"""The committed case-study bundles must equal a fresh run.

A published evidence file that nobody re-derives is a screenshot. These tests
re-run each drill into a temporary directory and compare the output against
the bytes in evidence/case-studies/, so the day the simulator changes and the
committed evidence does not, the suite goes red instead of the documentation
going quietly wrong.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "src" / "adapters" / "in_memory" / "run_drill.py"
CASE_STUDIES = ROOT / "evidence" / "case-studies"

# Directory name -> the drill whose runs it publishes.
REPRODUCIBLE = {
    "stale-writer": "stale-writer-completes",
    "duplicate-effect": "effect-commits-ack-is-lost",
}

EXPECTED_EXIT = {"unsafe": 2, "protected": 0}
EXPECTED_VERDICT = {"unsafe": "violation", "protected": "pass"}


def run_drill(drill, mode, out_dir):
    result = subprocess.run(
        [sys.executable, str(RUNNER), drill, "--mode", mode,
         "--out-dir", str(out_dir)],
        capture_output=True, text=True, check=False)
    return result, out_dir / f"{drill}-{mode}.json"


def test_every_reproducible_bundle_is_listed():
    """A new bundle directory must be pinned here, not appear untested."""
    on_disk = {p.name for p in CASE_STUDIES.iterdir()
               if p.is_dir() and any(p.glob("*.json"))}
    assert on_disk == set(REPRODUCIBLE), (
        "case-study directories with committed JSON and pinned drills "
        f"disagree: on disk {sorted(on_disk)}, pinned {sorted(REPRODUCIBLE)}")


def test_committed_bundles_match_a_fresh_run(tmp_path):
    for directory, drill in REPRODUCIBLE.items():
        for mode in ("unsafe", "protected"):
            result, produced = run_drill(drill, mode, tmp_path / directory)
            assert result.returncode == EXPECTED_EXIT[mode], (
                f"{drill} {mode}: exit {result.returncode}\n"
                f"{result.stdout}{result.stderr}")
            committed = CASE_STUDIES / directory / f"{mode}.json"
            assert committed.read_text() == produced.read_text(), (
                f"{committed.relative_to(ROOT)} is stale. Regenerate it:\n"
                f"  python3 src/adapters/in_memory/run_drill.py {drill} "
                f"--mode {mode}\n"
                f"  cp out/evidence/{drill}-{mode}.json {committed}")


def test_committed_bundles_carry_the_expected_verdicts():
    """The unsafe control has to violate, or the protected run proves nothing."""
    for directory in REPRODUCIBLE:
        for mode, verdict in EXPECTED_VERDICT.items():
            bundle = json.loads(
                (CASE_STUDIES / directory / f"{mode}.json").read_text())
            assert bundle["mode"] == mode
            assert bundle["oracle"]["verdict"] == verdict, (
                f"{directory}/{mode}.json: verdict "
                f"{bundle['oracle']['verdict']!r}, expected {verdict!r}")


def test_every_case_study_directory_has_a_readme():
    for path in sorted(CASE_STUDIES.iterdir()):
        if path.is_dir():
            assert (path / "README.md").is_file(), f"{path.name} has no README"
