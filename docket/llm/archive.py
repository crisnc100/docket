"""Exact request and answer files kept after the working folders are removed."""

import gzip
import hashlib
import json
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from docket.llm.exchange import Answer, PendingRequest, Provenance, validate_schema
from docket.llm.replay import ReplayStore, canonical


class ArchiveEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_json: str
    answer_json: str
    request_sha256: str
    answer_sha256: str
    provenance: Provenance

    def request(self) -> PendingRequest:
        return PendingRequest.model_validate_json(self.request_json)

    def answer(self) -> Answer:
        return Answer.model_validate_json(self.answer_json)

    def verify(self) -> None:
        for name in ("request", "answer"):
            raw = getattr(self, f"{name}_json").encode("utf-8")
            if hashlib.sha256(raw).hexdigest() != getattr(self, f"{name}_sha256"):
                raise ValueError(f"{self.key}: {name} bytes differ from their SHA-256")
        request, answer = self.request(), self.answer()
        request.verify_key()
        if request.key != self.key:
            raise ValueError("Archived request differs from its key")
        if answer.provenance != self.provenance:
            raise ValueError("Archived answer differs from its provenance")
        if answer.provenance.standing_in_for != request.target_model:
            raise ValueError("Archived answer targets a different model")
        canonical(answer.output)
        validate_schema(answer.output, request.response_schema)


def load_archive(path: Path) -> dict[str, ArchiveEntry]:
    entries = {}
    previous = ""
    with gzip.open(path, "rt", encoding="utf-8") as file:
        for line in file:
            entry = ArchiveEntry.model_validate_json(line)
            entry.verify()
            if entry.key <= previous:
                raise ValueError("Archive keys must be unique and sorted")
            entries[entry.key] = entry
            previous = entry.key
    return entries


def pack_exchange(pending: Path, answers: Path, archive: Path) -> int:
    entries = load_archive(archive) if archive.exists() else {}
    requests = {path.stem: path for path in pending.glob("*.json")}
    responses = {path.stem: path for path in answers.glob("*.json")}
    if (not requests and not entries) or requests.keys() != responses.keys():
        raise ValueError("Packing requires a nonempty request set with one answer per key")
    for key in sorted(requests):
        request_bytes, answer_bytes = requests[key].read_bytes(), responses[key].read_bytes()
        entry = ArchiveEntry(
            key=key,
            request_json=request_bytes.decode("utf-8"),
            answer_json=answer_bytes.decode("utf-8"),
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            answer_sha256=hashlib.sha256(answer_bytes).hexdigest(),
            provenance=Answer.model_validate_json(answer_bytes).provenance,
        )
        entry.verify()
        entries[key] = entry
    recordings = archive.with_name("calls.jsonl")
    if recordings.exists():
        _check_recordings(entries, recordings)
    return _write(archive, [entries[key].model_dump() for key in sorted(entries)])


def _write(archive: Path, rows: list[dict]) -> int:
    # Do not normalize strings: whitespace and line endings belong to the evidence.
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            with gzip.GzipFile(filename="", fileobj=file, mode="wb", mtime=0) as out:
                out.write(payload)
            file.flush()
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    return len(lines)


def check_archive(archive: Path, recordings: Path) -> int:
    entries = load_archive(archive)
    return _check_recordings(entries, recordings)


def _check_recordings(entries: dict[str, ArchiveEntry], recordings: Path) -> int:
    rows = ReplayStore(recordings).entries()
    for row in rows:
        if row.key not in entries:
            raise ValueError(f"Missing archive entry: {row.key}")
        answer = entries[row.key].answer()
        if answer.output != row.output or answer.provenance.model_dump() != row.provenance:
            raise ValueError(f"Archived answer differs from recording: {row.key}")
    return len(rows)


class LiveArchiveEntry(BaseModel):
    """The exact request sent for one live answer. The answer itself is in the recording."""

    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_json: str
    request_sha256: str

    def request(self) -> PendingRequest:
        return PendingRequest.model_validate_json(self.request_json)

    def verify(self) -> None:
        if hashlib.sha256(self.request_json.encode("utf-8")).hexdigest() != self.request_sha256:
            raise ValueError(f"{self.key}: request bytes differ from their SHA-256")
        request = self.request()
        request.verify_key()
        if request.key != self.key:
            raise ValueError("Archived request differs from its key")


def load_live_archive(path: Path) -> dict[str, LiveArchiveEntry]:
    entries = {}
    previous = ""
    with gzip.open(path, "rt", encoding="utf-8") as file:
        for line in file:
            entry = LiveArchiveEntry.model_validate_json(line)
            entry.verify()
            if entry.key <= previous:
                raise ValueError("Archive keys must be unique and sorted")
            entries[entry.key] = entry
            previous = entry.key
    return entries


def pack_live(pending: Path, recordings: Path, archive: Path) -> int:
    entries = load_live_archive(archive) if archive.exists() else {}
    for row in ReplayStore(recordings).entries():
        if row.key in entries:
            continue
        path = pending / f"{row.key}.json"
        if not path.exists():
            raise ValueError(f"Missing request for live recording: {row.key}")
        raw = path.read_bytes()
        entry = LiveArchiveEntry(
            key=row.key,
            request_json=raw.decode("utf-8"),
            request_sha256=hashlib.sha256(raw).hexdigest(),
        )
        entry.verify()
        entries[row.key] = entry
    _write(archive, [entries[key].model_dump() for key in sorted(entries)])
    _check_live(entries, recordings)
    return len(entries)


def check_live_archive(archive: Path, recordings: Path) -> int:
    return _check_live(load_live_archive(archive), recordings)


def _check_live(entries: dict[str, LiveArchiveEntry], recordings: Path) -> int:
    rows = ReplayStore(recordings).entries()
    for row in rows:
        if not row.live or row.provenance is None:
            raise ValueError(f"Not a live recording: {row.key}")
        if row.key not in entries:
            raise ValueError(f"Missing archive entry: {row.key}")
        if entries[row.key].request().target_model != row.provenance["model"]:
            raise ValueError(f"Archived request targets a different model: {row.key}")
    if set(entries) != {row.key for row in rows}:
        raise ValueError("The live archive holds requests with no recording")
    return len(rows)
