from temporalio import activity
from temporalio.exceptions import ApplicationError


class ScenarioActivities:
    """Scripted workflow-only inputs; these are not LLM replay recordings."""

    def __init__(self):
        self.calls = []
        self.finalized = []
        self.attempts = 0

    @activity.defn(name="extract")
    async def extract(self, stage: dict) -> dict:
        activity.heartbeat()
        self.calls.append("extract")
        case = stage["request"]["filename"]
        if case == "poison":
            raise ApplicationError(
                "corrupt", "corrupt", type="DocumentRejected", non_retryable=True
            )
        if case == "rejected":
            stage["rejected"] = "encrypted"
        if case == "failed":
            self.attempts += 1
            raise RuntimeError("bounded infrastructure failure")
        stage["text"] = stage["llm_text"] = "Synthetic workflow test input"
        return stage

    @activity.defn(name="shortlist")
    async def shortlist(self, stage: dict) -> dict:
        self.calls.append("shortlist")
        stage["candidates"] = [] if stage["request"]["filename"] == "empty" else ["UNIT-0001"]
        stage["valid_ids"] = ["UNIT-0001", "UNIT-0002"]
        return stage

    @activity.defn(name="classify")
    async def classify(self, stage: dict) -> dict:
        self.calls.append("classify")
        stage["category"] = "lease"
        return stage

    @activity.defn(name="choose")
    async def choose(self, stage: dict) -> dict:
        self.calls.append("choose")
        if stage["request"]["filename"] == "replay_miss":
            raise ApplicationError("missing recording", type="ReplayMiss", non_retryable=True)
        stage["chosen"] = "UNIT-0001"
        return stage

    @activity.defn(name="score")
    async def score(self, stage: dict) -> dict:
        self.calls.append("score")
        stage["suggested_record_id"] = stage["chosen"]
        auto = stage["request"]["filename"] in {"happy", "duplicate"}
        stage.update(
            score=".95" if auto else ".3",
            tier="strong" if auto else "medium",
            decision="file" if auto else "review",
            reason="threshold_met" if auto else "uncertain",
        )
        return stage

    @activity.defn(name="file")
    async def file(self, stage: dict) -> dict:
        self.calls.append("file")
        if stage["request"]["filename"] == "file_failed":
            raise ApplicationError("conflict", type="StorageConflict", non_retryable=True)
        return stage

    @activity.defn(name="finalize")
    async def finalize(self, stage: dict) -> dict:
        self.finalized.append((stage["terminal"], stage["reason"]))
        return stage

    def registered(self):
        return [
            self.extract,
            self.shortlist,
            self.classify,
            self.choose,
            self.score,
            self.file,
            self.finalize,
        ]
