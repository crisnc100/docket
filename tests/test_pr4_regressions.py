import json
from pathlib import Path

from docket.evaluation import write_report


def test_suggestion_report_states_the_actual_population(tmp_path):
    report = json.loads(Path("evals/history/simulated/reports/2026-11-11-seed-11.json").read_text())
    path = write_report(report, tmp_path)
    text = path.with_suffix(".md").read_text()
    assert "Documents that did not go to review are excluded." in text
    assert "Poison documents are excluded." not in text
    assert "Among 102 documents sent to review" in text
