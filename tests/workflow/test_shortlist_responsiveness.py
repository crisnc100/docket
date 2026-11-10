import asyncio
import threading
from time import perf_counter

from temporalio.testing import ActivityEnvironment

from docket.activities import Activities
from tests.matching_inputs import distinct_capped_text, double_records


async def test_seven_at_cap_shortlists_leave_event_loop_responsive(monkeypatch):
    records = double_records()
    text = distinct_capped_text()
    loop_thread = threading.get_ident()
    load_threads = []

    def load():
        load_threads.append(threading.get_ident())
        return records

    monkeypatch.setattr("docket.activities.load_records", load)
    activities = Activities(None)
    monkeypatch.setattr(activities, "event", lambda *args: None)
    environment = ActivityEnvironment()
    loop = asyncio.get_running_loop()
    timer = loop.create_future()
    started = perf_counter()
    handle = loop.call_later(0.05, lambda: timer.set_result(perf_counter() - started))
    tasks = [
        asyncio.create_task(
            environment.run(activities.shortlist, {"text": text, "request": {"filename": ""}})
        )
        for _ in range(7)
    ]
    try:
        elapsed = await timer
        results = await asyncio.gather(*tasks)
    finally:
        handle.cancel()
    print(f"event-loop timer during seven at-cap shortlists: {elapsed:.6f}s")
    assert elapsed < 1
    assert len(load_threads) == 7 and all(thread != loop_thread for thread in load_threads)
    assert all(not result["over_budget"] for result in results)
