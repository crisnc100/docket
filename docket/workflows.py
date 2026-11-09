import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError


@dataclass
class DocumentInput:
    document_id: str
    raw_key: str
    filename: str
    mode: str = "act"
    run_id: str = "live"
    source: str = "http"
    model_mode: str = "replay"
    model: str = "openai/gpt-4o-mini"
    budget: str = "2.00"
    review_timeout_seconds: float = 3600


@workflow.defn
class FileDocumentWorkflow:
    def __init__(self) -> None:
        self.review_open = False
        self.action: dict[str, str] | None = None
        self.valid_ids: list[str] = []
        self.state = "processing"

    async def step(self, name: str, stage: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            name,
            stage,
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1), backoff_coefficient=2, maximum_attempts=5
            ),
        )

    def accept(self, action: str, value: str) -> bool:
        if not self.review_open or self.action is not None:
            workflow.logger.info("Ignored duplicate or late review action: %s", action)
            return False
        if action == "approve" and value not in self.valid_ids:
            workflow.logger.info("Ignored unknown review record id")
            return False
        if action == "dismiss" and not value.strip():
            return False
        self.action = {"action": action, "value": value}
        self.review_open = False
        return True

    @workflow.signal
    def approve(self, record_id: str) -> None:
        self.accept("approve", record_id)

    @workflow.signal
    def dismiss(self, reason: str) -> None:
        self.accept("dismiss", reason)

    @workflow.update
    def review_action(self, action: dict[str, str]) -> bool:
        if action.get("action") not in {"approve", "dismiss"}:
            raise ApplicationError(
                "Unknown review action", type="ReviewConflict", non_retryable=True
            )
        if not self.accept(action["action"], action.get("value", "")):
            raise ApplicationError(
                "Duplicate, late or invalid review action",
                type="ReviewConflict",
                non_retryable=True,
            )
        return True

    @workflow.query
    def progress(self) -> dict[str, Any]:
        return {
            "status": self.state,
            "review_open": self.review_open,
            "valid_ids": self.valid_ids,
            "action": self.action,
        }

    @workflow.run
    async def run(self, request: DocumentInput) -> dict[str, Any]:
        stage: dict[str, Any] = {"request": request.__dict__}
        terminal, reason = "failed", "non_retryable_error"
        try:
            workflow.patched("review-contract-v1")
            stage = await self.step("extract", stage)
            if stage.get("rejected"):
                terminal, reason = "rejected", stage["rejected"]
            else:
                stage = await self.step("shortlist", stage)
                self.valid_ids = stage["valid_ids"]
                if stage["candidates"]:
                    stage = await self.step("classify", stage)
                    stage = await self.step("choose", stage)
                else:
                    stage.update(category="misc", chosen=None)
                stage = await self.step("score", stage)
                if stage["decision"] == "file":
                    if request.mode == "observe":
                        terminal, reason = "would_file", "threshold_met"
                    else:
                        stage = await self.step("file", stage)
                        terminal, reason = "filed", "threshold_met"
                elif request.mode == "observe":
                    terminal, reason = "would_review", stage["reason"]
                else:
                    stage["terminal"], stage["reason"] = "needs_review", stage["reason"]
                    await self.step("finalize", stage)
                    self.state, self.review_open = "needs_review", True
                    try:
                        await workflow.wait_condition(
                            lambda: self.action is not None,
                            timeout=timedelta(seconds=request.review_timeout_seconds),
                        )
                    except TimeoutError:
                        terminal, reason = "needs_review_expired", "review_timeout"
                    else:
                        assert self.action is not None
                        if self.action["action"] == "dismiss":
                            terminal, reason = "dismissed", self.action["value"]
                        else:
                            stage["chosen"] = self.action["value"]
                            stage["manual_approval"] = True
                            stage = await self.step("file", stage)
                            terminal, reason = "filed", "review_approved"
                    finally:
                        self.review_open = False
        except ActivityError as exc:
            cause = exc.cause
            if isinstance(cause, ApplicationError) and cause.type == "DocumentRejected":
                terminal, reason = "rejected", str(cause.details[0])
            else:
                terminal = "failed"
                reason = {
                    "ReplayMiss": "replay_miss",
                    "BudgetExceeded": "budget_exceeded",
                    "StorageConflict": "storage_conflict",
                    "ParseError": "parse_error",
                }.get(
                    getattr(cause, "type", ""),
                    "non_retryable_error"
                    if isinstance(cause, ApplicationError) and cause.non_retryable
                    else "retries_exhausted",
                )
        except asyncio.CancelledError:
            terminal, reason = "failed", "cancelled"
            raise
        finally:
            stage["terminal"], stage["reason"] = terminal, reason
            await asyncio.shield(self.step("finalize", stage))
            self.state = terminal
        return {
            "document_id": request.document_id,
            "status": terminal,
            "reason": reason,
            "record_id": stage.get("chosen"),
            "category": stage.get("category", "misc"),
            "score": stage.get("score", "0"),
            "tier": stage.get("tier", "none"),
        }
