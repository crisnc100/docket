import json

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
