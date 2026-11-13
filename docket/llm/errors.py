from temporalio.exceptions import ApplicationError


class GatewayError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, type=type(self).__name__, non_retryable=True)


class ParseError(GatewayError):
    pass


class ReplayMiss(GatewayError):
    pass


class RecordRefused(GatewayError):
    pass


class BudgetExceeded(GatewayError):
    pass


class UsageError(GatewayError):
    pass


class ExportPending(GatewayError):
    pass


UNREADABLE_REASONS = ("refusal", "cut", "not_json", "schema")


class UnreadableAnswer(ParseError):
    """The API answered and billed, but Docket cannot use the answer.

    It keeps the Temporal type ParseError. The classify step catches it and sends the
    document to review; a failed suggestion leaves no suggestion, as for any parse failure.
    """

    def __init__(self, reason: str, refusal_category: str | None = None) -> None:
        if reason not in UNREADABLE_REASONS:
            raise ValueError(f"Unknown unreadable reason: {reason}")
        if refusal_category is not None and reason != "refusal":
            raise ValueError("Only a refusal has a safety category")
        self.reason = reason
        # The API's own label for why its safety filter refused, such as "bio".
        self.refusal_category = refusal_category
        ApplicationError.__init__(
            self,
            f"Unreadable model answer: {reason}",
            reason,
            refusal_category,
            type="ParseError",
            non_retryable=True,
        )


class NoAnswer(GatewayError):
    """The request was never answered or billed, so it may be sent again."""


class MaybeBilled(GatewayError):
    """The connection failed after sending. It is charged at its worst case and never resent."""


class NotSent(Exception):
    """A transport failure the API did not bill: retry with backoff."""
