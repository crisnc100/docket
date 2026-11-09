import hashlib
import json

from docket.evaluation import simulation_summary, write_report
from docket.llm.replay import Recording, ReplayStore


def test_report_counts_saved_answers_and_keeps_operator_context(tmp_path):
    store = ReplayStore(tmp_path / "calls.jsonl")
    entries = []
    for i, simulator in enumerate(["test-small", "test-small", "test-fallback"]):
        entry = Recording(
            key=hashlib.sha256(str(i).encode()).hexdigest(),
            output={"record_id": "none"},
            cost_usd=None,
            source="synthetic_corpus",
            provenance={"simulated_by": simulator, "standing_in_for": "test-target"},
        )
        store.append(entry)
        entries.append(entry)
    reason = "The operator reports a false safety block on synthetic filler text."
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "archive": "calls.jsonl",
                "fallback_reason": reason,
                "method": "Each answer saw only its request.",
                "context_source": "Operator report",
                "simulator_counts": {"wrong-count": 999},
            }
        )
    )
    summary = simulation_summary(entries[:2], store)
    assert summary["seed_request_counts"] == {"test-small": 2}
    assert summary["archive_request_counts"] == {"test-small": 2, "test-fallback": 1}
    assert summary["fallback_reason"] == reason
    assert summary["context_source"] == "Operator report"
    supplied = simulation_summary(entries[:2], store, {entry.key for entry in entries})
    assert supplied["seed_request_counts"] == {"test-small": 2, "test-fallback": 1}
    assert supplied["used_request_counts"] == {"test-small": 2}
    report = {
        "date": "test",
        "seed": 11,
        "status": "PASS",
        "measurement": "simulated",
        "provenance": [{"simulated_by": "test-small", "standing_in_for": "test-target"}],
        "simulation": summary,
        "model_cost_usd": None,
        "floors": {},
        "docket": {"curve": []},
        "baseline": {"curve": [], "per_case_type": {}},
    }
    path = write_report(report, tmp_path / "reports")
    text = path.with_suffix(".md").read_text()
    assert text.startswith("Model responses simulated by test-small")
    assert "test-small: 2" in text and "test-fallback: 1" in text and reason in text
    assert json.loads(path.read_text())["simulation"] == summary
    assert json.loads(path.read_text())["model_cost_usd"] is None
    assert len(store.entries()) == 3


def test_context_for_another_archive_is_not_applied(tmp_path):
    store = ReplayStore(tmp_path / "calls.jsonl")
    store.path.touch()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "archive": "another.jsonl",
                "fallback_reason": "Unrelated run",
            }
        )
    )
    summary = simulation_summary([], store)
    assert summary == {
        "seed_request_counts": {},
        "used_request_counts": {},
        "archive_request_counts": {},
    }
