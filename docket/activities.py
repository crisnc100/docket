import asyncio
import contextlib
import uuid
from dataclasses import asdict
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from docket.activities_schema import Classification as Classification
from docket.activities_schema import choice_schema as choice_schema
from docket.config import Category, Signal, load_records
from docket.core.candidates import Candidate, CandidateResult
from docket.core.candidates import shortlist as build_shortlist
from docket.core.extraction import DocumentRejection, Rejected, extract_document
from docket.core.ledger import TERMINAL_STATUSES, append_event, transition
from docket.core.scoring import load_scoring_config, score_choice
from docket.core.storage import StorageConflict, build_key
from docket.db import Document, FiledDocument
from docket.failpoints import fire
from docket.prompts import choose_request, classify_request
from docket.runtime import Runtime


# Blocking work must run in a thread: a blocked event loop stops these beats, and
# Temporal retries the activity once the heartbeat timeout passes.
@contextlib.asynccontextmanager
async def heartbeats():
    async def beat():
        while True:
            activity.heartbeat()
            await asyncio.sleep(3)

    task = asyncio.create_task(beat())
    try:
        activity.heartbeat()
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class Activities:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    def event(self, stage: dict, step: str, detail: dict) -> None:
        with Session(self.runtime.engine) as session, session.begin():
            append_event(session, uuid.UUID(stage["request"]["document_id"]), step, detail)

    @activity.defn(name="extract")
    async def extract(self, stage: dict) -> dict:
        async with heartbeats():

            def work():
                with Session(self.runtime.engine) as session, session.begin():
                    transition(
                        session,
                        uuid.UUID(stage["request"]["document_id"]),
                        "processing",
                        "workflow_started",
                    )
                try:
                    result = extract_document(self.runtime.read(stage["request"]["raw_key"]))
                except DocumentRejection as exc:
                    raise ApplicationError(
                        str(exc), exc.reason.value, type="DocumentRejected", non_retryable=True
                    ) from exc
                if isinstance(result, Rejected):
                    stage["rejected"] = result.reason.value
                else:
                    stage.update(text=result.text, llm_text=result.llm_text)
                self.event(
                    stage,
                    "extract",
                    {"rejected": stage.get("rejected"), "characters": len(stage.get("text", ""))},
                )
                return stage

            return await asyncio.to_thread(work)

    @activity.defn(name="shortlist")
    async def shortlist(self, stage: dict) -> dict:
        async with heartbeats():

            def work():
                records = load_records()
                result = build_shortlist(
                    records, text=stage["text"], filename=stage["request"]["filename"]
                )
                return records, result

            records, result = await asyncio.to_thread(work)
            stage.update(
                candidates=[asdict(candidate) for candidate in result.candidates],
                conflict=result.conflict,
                explicit_ids=list(result.explicit_ids),
                conflict_record_ids=list(result.conflict_record_ids),
                over_budget=result.over_budget,
                valid_ids=[record.record_id for record in records],
            )
            await asyncio.to_thread(
                self.event,
                stage,
                "shortlist",
                {"candidates": stage["candidates"], "conflict": result.conflict},
            )
            return stage

    @activity.defn(name="classify")
    async def classify(self, stage: dict) -> dict:
        async with heartbeats():
            gateway = self.runtime.gateway(stage["request"])
            result = await gateway.call(
                **classify_request(stage["llm_text"]),
                source=stage["request"]["source"],
            )
            stage["category"] = result.category.value
            await asyncio.to_thread(self.event, stage, "classify", {"category": stage["category"]})
            return stage

    @activity.defn(name="choose")
    async def choose(self, stage: dict) -> dict:
        async with heartbeats():
            if not stage["candidates"]:
                stage["chosen"] = None
                return stage
            fire("choose.during")
            ids = [candidate["record_id"] for candidate in stage["candidates"]]
            result = await self.runtime.gateway(stage["request"]).call(
                **choose_request(stage["llm_text"], ids),
                source=stage["request"]["source"],
            )
            choice = result.model_dump(mode="json")["record_id"]
            stage["chosen"] = None if choice == "none" else choice
            await asyncio.to_thread(self.event, stage, "choose", {"record_id": stage["chosen"]})
            return stage

    @activity.defn(name="score")
    async def score(self, stage: dict) -> dict:
        async with heartbeats():
            candidates = tuple(
                Candidate(
                    item["record_id"],
                    {Signal(key): value for key, value in item["signals"].items()},
                    item["former_tenant_score"],
                    item["building_is_explicit"],
                )
                for item in stage["candidates"]
            )
            shortlist = CandidateResult(
                candidates,
                stage["conflict"],
                tuple(stage["explicit_ids"]),
                tuple(stage["conflict_record_ids"]),
                stage.get("over_budget", False),
            )
            config = load_scoring_config()
            scored = [score_choice(shortlist, item.record_id, config) for item in candidates]
            scored.sort(key=lambda item: item.score, reverse=True)
            result = scored[0] if scored else score_choice(shortlist, None, config)
            # The model's pick is kept only as a suggestion for the reviewer. The rules' top
            # score decides the tier and whether the document files.
            stage["suggested_record_id"] = stage["chosen"]
            stage["chosen"] = result.record_id
            candidate_scores = [
                {
                    "record_id": item.record_id,
                    "score": str(item.score),
                    "tier": item.tier.value,
                    "signals": next(c.signals for c in candidates if c.record_id == item.record_id),
                }
                for item in scored
            ]
            stage.update(
                score=str(result.score),
                tier=result.tier.value,
                decision=result.decision,
                reason=result.reason,
            )
            await asyncio.to_thread(
                self.event,
                stage,
                "score",
                {
                    **{key: stage[key] for key in ["score", "tier", "decision", "reason"]},
                    "rules_top_record_id": result.record_id,
                    "suggested_record_id": stage["suggested_record_id"],
                    "candidates": candidate_scores,
                },
            )
            return stage

    @activity.defn(name="file")
    async def file(self, stage: dict) -> dict:
        async with heartbeats():

            def work():
                request = stage["request"]
                if request["mode"] != "act":
                    raise ApplicationError("Observe mode cannot file", non_retryable=True)
                if not stage.get("manual_approval") and (
                    stage.get("tier") != "strong" or stage.get("decision") != "file"
                ):
                    raise ApplicationError(
                        "Automatic filing requires strong evidence", non_retryable=True
                    )
                record = next(
                    record for record in load_records() if record.record_id == stage["chosen"]
                )
                with Session(self.runtime.engine) as session:
                    document = session.get(Document, uuid.UUID(request["document_id"]))
                    assert document is not None
                    content_hash = document.content_hash
                key = build_key(record, Category(stage["category"]), content_hash)
                try:
                    self.runtime.storage.put_if_absent(
                        key,
                        self.runtime.read(request["raw_key"]),
                        content_hash,
                        original_filename=request["filename"],
                    )
                except StorageConflict as exc:
                    raise ApplicationError(
                        str(exc), type="StorageConflict", non_retryable=True
                    ) from exc
                fire("file.after_storage")
                with Session(self.runtime.engine) as session, session.begin():
                    document = session.get(
                        Document,
                        uuid.UUID(request["document_id"]),
                        with_for_update=True,
                        populate_existing=True,
                    )
                    assert document is not None
                    session.execute(
                        insert(FiledDocument)
                        .values(
                            document_id=document.id,
                            mode=request["mode"],
                            run_id=request["run_id"],
                            content_hash=content_hash,
                            record_id=record.record_id,
                            category=stage["category"],
                            storage_key=key,
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                FiledDocument.mode,
                                FiledDocument.run_id,
                                FiledDocument.content_hash,
                            ]
                        )
                    )
                    document.record_id, document.category = record.record_id, stage["category"]
                    document.score = Decimal(stage["score"])
                    append_event(session, document.id, "file", {"key": key})
                    transition(
                        session,
                        document.id,
                        "filed",
                        "review_approved" if stage.get("manual_approval") else "threshold_met",
                    )
                stage["storage_key"] = key
                return stage

            return await asyncio.to_thread(work)

    @activity.defn(name="finalize")
    async def finalize(self, stage: dict) -> dict:
        async with heartbeats():

            def work():
                with Session(self.runtime.engine) as session, session.begin():
                    document = session.get(
                        Document,
                        uuid.UUID(stage["request"]["document_id"]),
                        with_for_update=True,
                        populate_existing=True,
                    )
                    assert document is not None
                    if document.status not in TERMINAL_STATUSES:
                        document.record_id, document.category = (
                            stage.get("chosen"),
                            stage.get("category"),
                        )
                        document.score = Decimal(stage["score"]) if "score" in stage else None
                        transition(session, document.id, stage["terminal"], stage["reason"])
                return stage

            return await asyncio.to_thread(work)

    def registered(self) -> list:
        return [
            self.extract,
            self.classify,
            self.shortlist,
            self.choose,
            self.score,
            self.file,
            self.finalize,
        ]
