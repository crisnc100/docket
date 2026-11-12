"""A model call, with every retry, must end well before Temporal's activity deadline.

Otherwise Temporal times the activity out and runs it again, which can resend a request
the API may already have billed.
"""

from datetime import timedelta

from docket.llm.anthropic import REQUEST_TIMEOUT_SECONDS, AnthropicTransport
from docket.llm.errors import MaybeBilled, NoAnswer, UnreadableAnswer
from docket.llm.gateway import RETRY_DELAYS, gateway_worst_seconds
from docket.workflows import MODEL_ACTIVITIES, MODEL_ACTIVITY_TIMEOUT, activity_timeout
from tests.test_anthropic_transport import settings

# Room for the budget reservation, the database writes and the worker's own setup.
MARGIN = timedelta(seconds=20)


def test_model_call_with_all_retries_ends_before_the_activity_deadline():
    worst = gateway_worst_seconds(REQUEST_TIMEOUT_SECONDS)
    assert worst == 5 * REQUEST_TIMEOUT_SECONDS + sum(RETRY_DELAYS)
    assert timedelta(seconds=worst) + MARGIN <= MODEL_ACTIVITY_TIMEOUT
    assert REQUEST_TIMEOUT_SECONDS * 2 <= MODEL_ACTIVITY_TIMEOUT.total_seconds() / 4


def test_model_activities_get_the_model_deadline():
    assert MODEL_ACTIVITIES == {"classify", "choose"}
    for name in MODEL_ACTIVITIES:
        assert activity_timeout(name) == MODEL_ACTIVITY_TIMEOUT
    assert activity_timeout("extract") == timedelta(seconds=30)


def test_the_transport_uses_the_request_timeout():
    transport = AnthropicTransport(settings(), 256)
    assert transport._client.timeout == REQUEST_TIMEOUT_SECONDS


def test_outcomes_that_must_not_be_resent_are_non_retryable_in_temporal():
    for error in [MaybeBilled("dropped"), NoAnswer("limited"), UnreadableAnswer("cut")]:
        assert error.non_retryable is True
