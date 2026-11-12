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

    It keeps the Temporal type ParseError, so workflows treat it like any other
    parse failure: a failed category fails the document, a failed suggestion does not.
    """

    def __init__(self, reason: str) -> None:
        if reason not in UNREADABLE_REASONS:
            raise ValueError(f"Unknown unreadable reason: {reason}")
        self.reason = reason
        ApplicationError.__init__(
            self,
            f"Unreadable model answer: {reason}",
            reason,
            type="ParseError",
            non_retryable=True,
        )


class NoAnswer(GatewayError):
    """The request was never answered or billed, so it may be sent again."""


class MaybeBilled(GatewayError):
    """The connection failed after sending. It is charged at its worst case and never resent."""


class NotSent(Exception):
    """A transport failure the API did not bill: retry with backoff."""
