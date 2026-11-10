import fcntl
import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docket.llm.errors import ParseError, ReplayMiss


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


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    output: dict[str, Any]
    cost_usd: str
    source: str = Field(pattern=r"^synthetic_corpus$")


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
            file.write(entry.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())
