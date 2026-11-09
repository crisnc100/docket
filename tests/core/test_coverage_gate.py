import json
import subprocess
import sys

import pytest

MODULES = ["candidates", "scoring", "ledger", "storage"]


def report():
    return {
        "meta": {"branch_coverage": True},
        "files": {
            f"docket/core/{module}.py": {
                "summary": {"num_branches": 100, "covered_branches": 100, "percent_covered": 100}
            }
            for module in MODULES
        },
    }


def run(tmp_path, data):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(data))
    return subprocess.run(
        [sys.executable, "scripts/check_coverage.py", str(path)], capture_output=True, text=True
    )


def test_gate_accepts_branch_floors_exactly(tmp_path):
    data = report()
    for module in MODULES:
        data["files"][f"docket/core/{module}.py"]["summary"]["covered_branches"] = (
            90 if module == "storage" else 95
        )
    assert run(tmp_path, data).returncode == 0


@pytest.mark.parametrize("module", MODULES)
def test_gate_rejects_low_branch_coverage_even_if_combined_coverage_passes(tmp_path, module):
    data = report()
    data["files"][f"docket/core/{module}.py"]["summary"]["covered_branches"] = (
        89 if module == "storage" else 94
    )
    result = run(tmp_path, data)
    assert result.returncode == 1 and f"FAIL docket/core/{module}.py" in result.stdout


@pytest.mark.parametrize("problem", ["missing", "no_branches", "not_branch", "invalid_counters"])
def test_gate_rejects_incomplete_reports(tmp_path, problem):
    data = report()
    if problem == "missing":
        del data["files"]["docket/core/scoring.py"]
    elif problem == "no_branches":
        data["files"]["docket/core/scoring.py"]["summary"].update(
            num_branches=0, covered_branches=0
        )
    elif problem == "not_branch":
        data["meta"]["branch_coverage"] = False
    else:
        data["files"]["docket/core/scoring.py"]["summary"]["covered_branches"] = 101
    assert run(tmp_path, data).returncode == 1


def test_gate_handles_missing_or_malformed_json(tmp_path):
    missing = subprocess.run(
        [sys.executable, "scripts/check_coverage.py", str(tmp_path / "missing")],
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 1 and "invalid coverage report" in missing.stderr
    assert run(tmp_path, {}).returncode == 1
