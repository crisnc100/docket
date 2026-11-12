import fcntl
import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docket.llm.errors import UNREADABLE_REASONS, ParseError, ReplayMiss


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    if isinstance(value, dict):
        return {normalize(key): normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def canonical(value: Any) -> str:
    return json.dumps(
        normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def replay_key(activity: str, prompt_version: str, model: str, inputs: Any) -> str:
    digest = hashlib.sha256(canonical(inputs).encode()).hexdigest()
    return hashlib.sha256(canonical([activity, prompt_version, model, digest]).encode()).hexdigest()


V01_FIELDS = {"key", "output", "cost_usd", "source", "provenance"}
LIVE_FIELDS = ("raw_text", "cost_usd", "input_tokens", "output_tokens", "latency_ms", "output_mode")


class Recording(BaseModel):
    """One answer. Simulated entries carry only key, output, cost, source and provenance."""

    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["answer", "unreadable"] = "answer"
    reason: str | None = None
    output: dict[str, Any] | None
    raw_text: str | None = None
    cost_usd: str | None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    output_mode: Literal["json_schema", "json_prompt"] | None = None
    source: str = Field(pattern=r"^synthetic_corpus$")
    provenance: dict[str, str] | None = None

    @model_validator(mode="after")
    def consistent(self) -> "Recording":
        if self.outcome == "answer" and (self.output is None or self.reason is not None):
            raise ValueError("An answer needs an output and no reason")
        if self.outcome == "unreadable" and (
            self.output is not None or self.reason not in UNREADABLE_REASONS
        ):
            raise ValueError("An unreadable answer needs a known reason and no output")
        if self.live and any(getattr(self, name) is None for name in LIVE_FIELDS):
            raise ValueError("A live answer needs its raw text, cost, tokens, latency and mode")
        return self

    @property
    def live(self) -> bool:
        return bool(self.provenance and "provider" in self.provenance)

    def line(self) -> str:
        # Simulated entries keep their v0.1 shape; live entries always write every field.
        if self.live:
            return self.model_dump_json()
        return self.model_dump_json(include=V01_FIELDS)


class ReplayStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _read(file) -> dict[str, Recording]:
        recordings: dict[str, Recording] = {}
        for line in file:
            try:
                entry = Recording.model_validate_json(line)
            except ValueError as exc:
                raise ParseError("Malformed replay JSONL") from exc
            if entry.key in recordings and entry != recordings[entry.key]:
                raise ParseError("Conflicting replay entries")
            recordings[entry.key] = entry
        return recordings

    def get(self, key: str) -> Recording:
        try:
            with self.path.open() as file:
                fcntl.flock(file, fcntl.LOCK_SH)
                entries = self._read(file)
        except FileNotFoundError as exc:
            raise ReplayMiss(f"No recording for {key}") from exc
        if key not in entries:
            raise ReplayMiss(f"No recording for {key}")
        return entries[key]

    def contains(self, key: str) -> bool:
        try:
            self.get(key)
        except ReplayMiss:
            return False
        return True

    def entries(self) -> list[Recording]:
        with self.path.open() as file:
            fcntl.flock(file, fcntl.LOCK_SH)
            return list(self._read(file).values())

    def append(self, entry: Recording) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            existing = self._read(file)
            if entry.key in existing:
                if existing[entry.key] != entry:
                    raise ParseError("Refusing to overwrite a replay entry")
                return
            file.seek(0, os.SEEK_END)
            file.write(entry.line() + "\n")
            file.flush()
            os.fsync(file.fileno())
