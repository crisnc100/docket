from temporalio.testing import ActivityEnvironment

from docket.activities import Activities
from docket.config import load_records


async def test_shortlist_activity_preserves_input_budget_for_score_activity(monkeypatch):
    record = load_records()[0]
    activities = Activities(None)
    monkeypatch.setattr(activities, "event", lambda *args: None)
    environment = ActivityEnvironment()
    stage = {
        "text": record.record_id + "\n" + "x" * 64001,
        "request": {"filename": record.address + ".pdf"},
        "chosen": record.record_id,
    }
    stage = await environment.run(activities.shortlist, stage)
    assert stage["over_budget"]
    stage = await environment.run(activities.score, stage)
    assert stage["decision"] == "review"
    assert stage["reason"] == "input_too_large"
