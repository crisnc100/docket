from time import process_time, sleep

import pytest

from tests.core import test_matching_input_budget as benchmark


def test_matching_budget_ignores_time_without_cpu(monkeypatch):
    real_shortlist = benchmark.shortlist

    def descheduled(*args, **kwargs):
        sleep(1.1)
        return real_shortlist(*args, **kwargs)

    monkeypatch.setattr(benchmark, "shortlist", descheduled)
    benchmark.test_matching_at_both_caps_with_double_records_finishes_under_one_second("unit_lines")


def test_matching_budget_still_interrupts_excess_cpu(monkeypatch):
    real_shortlist = benchmark.shortlist

    def excessive_cpu(*args, **kwargs):
        started = process_time()
        while process_time() - started < 1.1:
            pass
        return real_shortlist(*args, **kwargs)

    monkeypatch.setattr(benchmark, "shortlist", excessive_cpu)
    with pytest.raises(TimeoutError, match="one CPU second"):
        benchmark.test_matching_at_both_caps_with_double_records_finishes_under_one_second(
            "unit_lines"
        )
