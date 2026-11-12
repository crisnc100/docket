"""The Anthropic Messages API, called with the official SDK.

Each request sends the system prompt as `system` and the document in the one user
turn. The JSON schema goes in `output_config.format`, with its refs inlined and every
object closed, because structured output requires both. Thinking is off and effort is
low: choosing from a short list needs no thinking, and thinking tokens bill as output.
No temperature, top_p or top_k: the API refuses non-default values for this model.

If the API refuses the schema itself, the transport asks for JSON in the prompt for the
rest of the process. The gateway validates the answer either way, and the mode in use
is returned with every reply.

The SDK's own retries are off. The gateway retries what raises NotSent; anything that
may have reached the API is raised as MaybeBilled and never resent.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import anthropic
import httpx2
from pydantic import BaseModel

from docket.llm.errors import MaybeBilled, NotSent
from docket.settings import Settings

type OutputModeName = Literal["json_schema", "json_prompt"]
JSON_SCHEMA: OutputModeName = "json_schema"
JSON_PROMPT: OutputModeName = "json_prompt"
# Words in a 400's message that mean the API refused the output schema, not the request.
SCHEMA_REFUSED = ("schema", "grammar", "output_config", "format")
# Statuses that mean the API did not process the request. Any other 5xx, and a 408,
# may come after the API did the work (a 504 from a gateway, say), so it may be billed.
NOT_PROCESSED = frozenset({429, 500, 503, 529})
# These httpx2 errors happen while connecting, so the request was never sent.
NOT_SENT = (httpx2.ConnectError, httpx2.ConnectTimeout)

# Kept well under the model activities' Temporal deadline, with every retry included
# (tests/test_model_timeouts.py). Past that deadline Temporal would run the activity
# again and could resend a request the API already billed.
REQUEST_TIMEOUT_SECONDS = 15.0

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reply:
    text: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    output_mode: OutputModeName
    latency_ms: int


class OutputMode:
    """Shared by every transport in the process, so one schema refusal switches them all."""

    def __init__(self) -> None:
        self.current: OutputModeName = JSON_SCHEMA

    def fall_back(self, message: str) -> None:
        if self.current == JSON_SCHEMA:
            self.current = JSON_PROMPT
            log.warning("Structured output refused (%s); asking for JSON in the prompt", message)


OUTPUT_MODE = OutputMode()


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    root = schema.model_json_schema()
    definitions = root.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].removeprefix("#/$defs/")
            return walk({**definitions[name], **{k: v for k, v in node.items() if k != "$ref"}})
        result = {key: walk(value) for key, value in node.items()}
        if result.get("type") == "object":
            result["additionalProperties"] = False
        return result

    return walk(root)


class AnthropicTransport:
    provider = "anthropic"

    def __init__(
        self,
        settings: Settings,
        max_output_tokens: int,
        http_client: httpx2.AsyncClient | None = None,
        *,
        mode: OutputMode = OUTPUT_MODE,
        clock: Callable[[], float] = time.monotonic,
    ):
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is required for live calls")
        self.model = settings.docket_model
        self.max_output_tokens = max_output_tokens
        self.mode = mode
        self.clock = clock
        self._client = anthropic.AsyncAnthropic(
            api_key=key,
            max_retries=0,
            timeout=REQUEST_TIMEOUT_SECONDS,
            http_client=http_client,
        )

    def __repr__(self) -> str:
        return f"AnthropicTransport(model={self.model!r}, mode={self.mode.current!r})"

    def body(self, prompt: str, schema: type[BaseModel], system_prompt: str) -> dict[str, Any]:
        strict = strict_schema(schema)
        config: dict[str, Any] = {"effort": "low"}
        system = system_prompt
        if self.mode.current == JSON_SCHEMA:
            config["format"] = {"type": "json_schema", "schema": strict}
        else:
            system = (
                (f"{system_prompt}\n\n" if system_prompt else "")
                + "Reply with one JSON object that matches this schema, and nothing else:\n"
                + (json.dumps(strict, sort_keys=True))
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "disabled"},
            "output_config": config,
        }
        if system:
            body["system"] = system
        return body

    async def __call__(
        self, prompt: str, schema: type[BaseModel], *, system_prompt: str = ""
    ) -> Reply:
        try:
            return await self._send(prompt, schema, system_prompt)
        except anthropic.BadRequestError as exc:
            message = _message(exc)
            if self.mode.current != JSON_SCHEMA or not any(
                word in message.lower() for word in SCHEMA_REFUSED
            ):
                raise
            # A 400 is not billed, so asking again in the other mode costs nothing extra.
            self.mode.fall_back(message)
            return await self._send(prompt, schema, system_prompt)

    async def _send(self, prompt: str, schema: type[BaseModel], system_prompt: str) -> Reply:
        mode = self.mode.current
        started = self.clock()
        try:
            message = await self._client.messages.create(**self.body(prompt, schema, system_prompt))
        except anthropic.APIStatusError as exc:
            if exc.status_code in NOT_PROCESSED:
                raise NotSent(f"HTTP {exc.status_code}") from exc
            if exc.status_code == 408 or exc.status_code >= 500:
                raise MaybeBilled(f"HTTP {exc.status_code} may follow processing") from exc
            raise
        except anthropic.APIConnectionError as exc:
            if isinstance(exc.__cause__, NOT_SENT):
                raise NotSent(type(exc.__cause__).__name__) from exc
            raise MaybeBilled(f"{type(exc).__name__} after sending") from exc
        latency_ms = round((self.clock() - started) * 1000)
        usage = message.usage
        text = "".join(block.text for block in message.content if block.type == "text")
        return Reply(
            text=text,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0),
            output_tokens=usage.output_tokens,
            output_mode=mode,
            latency_ms=latency_ms,
        )


def _message(exc: anthropic.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
    return str(exc.message)[:300]
