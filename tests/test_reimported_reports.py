import hashlib
import json
from decimal import Decimal
from pathlib import Path

from docket.core.extraction import extract_document
from docket.core.scoring import load_scoring_config
from docket.evaluation import write_report


def test_simulated_banner_names_counts_and_keeps_archive_separate(tmp_path):
    report = {
        "date": "2026-11-10",
        "seed": 7,
        "status": "PENDING-SIMULATION",
        "baseline": None,
        "measurement": "simulated",
        "provenance": [{"simulated_by": "simulator", "standing_in_for": "target"}],
        "simulation": {
            "seed_request_counts": {"simulator": 2},
            "used_request_counts": {"simulator": 2},
            "archive_request_counts": {"simulator": 3},
        },
    }
    path = write_report(report, tmp_path)
    data = json.loads(path.read_text())
    text = path.with_suffix(".md").read_text()
    assert text.startswith(
        "Model responses simulated by simulator (2 unique requests in this seed), "
        "standing in for target; not an OpenRouter measurement."
    )
    assert "Replay archive, including superseded requests: simulator: 3." in text
    assert "Both seeds: simulator: 3." not in text
    assert "(2 unique requests in this seed)" in data["banner"]


def test_corpus_guard_changes_only_the_three_accepted_tenant_payloads():
    old = json.loads(Path("corpus/history/original-digests-pre-core.json").read_text())
    current = json.loads(Path("corpus/original-digests.json").read_text())
    expected = {
        "corpus/seed-7/doc-0068-very_long.pdf",
        "corpus/seed-11/doc-0035-clean.pdf",
        "corpus/seed-11/doc-0045-exact_duplicate.pdf",
    }
    assert old.keys() == current.keys() and len(current) == 342
    assert {path for path in old if old[path] != current[path]} == expected
    for filename in expected:
        payload = Path(filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == current[filename]
        text = extract_document(payload).text
        assert "Tenant: Norma Fisher" in text and "Tenant: Brian Young" not in text


def test_seed7_revalidation_preserves_threshold_and_accounts_for_one_corrected_filing():
    old = json.loads(Path("evals/history/threshold-selection-pre-core.json").read_text())
    current = json.loads(Path("evals/threshold-selection.json").read_text())
    assert current["seed"] == old["seed"] == 7
    assert current["rule_sha256"] == old["rule_sha256"]
    assert current["selected_threshold"] == old["selected_threshold"]
    assert Decimal(current["selected_threshold"]) == load_scoring_config().auto_file_threshold
    assert current["historical_selection"] == "evals/history/threshold-selection-pre-core.json"
    old_trials = {trial["threshold"]: trial for trial in old["trials"]}
    new_trials = {trial["threshold"]: trial for trial in current["trials"]}
    assert old_trials.keys() == new_trials.keys()
    for threshold, trial in new_trials.items():
        previous = old_trials[threshold]
        expected_gain = int(Decimal(threshold) <= Decimal(".95"))
        assert trial["correct_file_count"] == previous["correct_file_count"] + expected_gain
        assert trial["wrong_file_count"] == previous["wrong_file_count"] == 0
        assert trial["clean_auto_file_rate"] == previous["clean_auto_file_rate"]
