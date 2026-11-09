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
