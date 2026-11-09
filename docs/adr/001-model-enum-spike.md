# ADR 001: OpenRouter per-call enum output

Status: PENDING — no live key is available in the environment or `.env`.

## Prediction

The configured cheap default `openai/gpt-4o-mini`, pinned to OpenAI with
`require_parameters=true` and provider fallbacks disabled, will honour a strict
`json_schema` whose allowed record ids change between calls. Two calls with
different record ids will both return the requested member of their own enum.

## Experiment

`uv run docket spike-models` performs the two synthetic calls with a $0.50 run
cap and the persisted daily global cap. It writes measured model, provider,
enum values, choices, call count and OpenRouter usage cost to
`docs/adr/model-spike-result.json`. No input document or API key is recorded.
The script fails if either output is invalid or differs from the requested id.

## Result

PENDING. No live requests were made and no result file is claimed. Local HTTP
transport tests establish the request shape and usage parsing only; they do not
establish provider compliance. A successful live experiment writes its measured
result here and retains the JSON evidence. A failing provider/model requires a
callback rather than a silent fallback.

## Decision

Use strict per-call JSON schemas and revalidate the result with Pydantic. The
local parse fallback handles valid JSON content when structured parsing is
unavailable; it does not broaden the enum. Unparseable output is a non-retryable
`ParseError`. No alternative model or provider has been measured.

Protocol references:
[structured output](https://openrouter.ai/docs/guides/features/structured-outputs),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[usage cost](https://openrouter.ai/docs/api/reference/overview).
