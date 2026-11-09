"""Offline request exchange; answers are supplied by the operator."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from docket.llm.errors import ParseError, RecordRefused
from docket.llm.replay import Recording, ReplayStore, canonical, replay_key


class PendingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    activity: str
    prompt_version: str
    target_model: str
    system_prompt: str
    messages: list[dict[str, str]]
    response_schema: dict[str, Any]
    inputs: Any
    source: str = Field(pattern=r"^synthetic_corpus$")
    key_annotation: Literal["public request digest # pragma: allowlist secret"] = (
        "public request digest # pragma: allowlist secret"
    )

    def verify_key(self) -> None:
        if (
            len(self.messages) != 1
            or set(self.messages[0]) != {"role", "content"}
            or self.messages[0]["role"] != "user"
        ):
            raise ParseError("Request must contain exactly one user message")
        request = {
            "inputs": self.inputs,
            "prompt": self.messages[0]["content"],
            "schema": self.response_schema,
        }
        if self.system_prompt:
            request["system_prompt"] = self.system_prompt
        if replay_key(self.activity, self.prompt_version, self.target_model, request) != self.key:
            raise ParseError("Pending request does not match its digest")


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simulated_by: str = Field(min_length=1)
    standing_in_for: str = Field(min_length=1)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output: dict[str, Any]
    provenance: Provenance


def validate_schema(value: Any, schema: dict, root: dict | None = None) -> None:
    """Validate the JSON Schema subset emitted by the product's Pydantic models.

    Unsupported validation keywords fail closed; no optional validator dependency
    is needed for these object/string/enum contracts.
    """
    root = root or schema
    supported = {
        "$ref",
        "$defs",
        "title",
        "description",
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "anyOf",
        "default",
    }
    if set(schema) - supported:
        raise ParseError("Unsupported response-schema constraint")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/") or ref[8:] not in root.get("$defs", {}):
            raise ParseError("Invalid response-schema reference")
        validate_schema(value, root["$defs"][ref[8:]], root)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate_schema(value, option, root)
                break
            except ParseError:
                pass
        else:
            raise ParseError("Answer does not match any allowed schema")
    if "enum" in schema and value not in schema["enum"]:
        raise ParseError("Answer is outside the request enum")
    if "const" in schema and value != schema["const"]:
        raise ParseError("Answer differs from the required constant")
    kind = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": type(value) is int,
        "number": type(value) in {int, float},
    }
    if kind is not None and not valid.get(kind, False):
        raise ParseError("Answer has the wrong JSON type")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ParseError("Answer is missing a required field")
        for name, item in value.items():
            if name in properties:
                validate_schema(item, properties[name], root)
            elif schema.get("additionalProperties") is False:
                raise ParseError("Answer contains an unexpected field")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"], root)
    if kind == "array":
        for item in value:
            validate_schema(item, schema.get("items", {}), root)


class Exchange:
    def __init__(self, pending: Path, answers: Path):
        self.pending, self.answers = pending, answers

    def export(self, request: PendingRequest) -> None:
        if request.source != "synthetic_corpus":
            raise RecordRefused("Export accepts only synthetic corpus requests")
        request.verify_key()
        self.pending.mkdir(parents=True, exist_ok=True)
        path = self.pending / f"{request.key}.json"
        if path.exists():
            if PendingRequest.model_validate_json(path.read_text()) != request:
                raise ParseError("Refusing to overwrite a different pending request")
        # Scope the scanner annotation to the verified public digest's line;
        # prompts and schemas remain fully scanned on their own lines.
        body = request.model_dump_json(indent=2, exclude={"key_annotation"})
        body = body.replace(
            f'"key": "{request.key}",',
            f'"key": "{request.key}", "key_annotation": "{request.key_annotation}",',
            1,
        )
        path.write_text(body + "\n")

    def import_answers(self, keys: list[str], store: ReplayStore) -> dict[str, int]:
        entries = []
        missing = 0
        for key in sorted(set(keys)):
            if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
                raise ParseError("Invalid pending request key")
            request = PendingRequest.model_validate_json((self.pending / f"{key}.json").read_text())
            request.verify_key()
            if request.key != key:
                raise ParseError("Request filename does not match its key")
            path = self.answers / f"{key}.json"
            if not path.exists():
                missing += 1
                continue
            try:
                answer = Answer.model_validate_json(path.read_text())
                canonical(answer.output)
            except ValueError as exc:
                raise ParseError("Malformed simulation answer") from exc
            if answer.provenance.standing_in_for != request.target_model:
                raise ParseError("Answer provenance targets a different model")
            validate_schema(answer.output, request.response_schema)
            entries.append(
                Recording(
                    key=key,
                    output=answer.output,
                    cost_usd=None,
                    provenance=answer.provenance.model_dump(),
                    source=request.source,
                )
            )
        # Validate the entire batch before admitting any answer to the replay store.
        for entry in entries:
            store.append(entry)
        return {"imported": len(entries), "pending": missing}


def read_manifest(seed: int, directory: Path = Path("evals/manifests")) -> dict:
    return json.loads((directory / f"seed-{seed}.json").read_text())
