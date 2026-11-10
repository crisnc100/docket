# ADR 001: OpenRouter per-call enum output

Status: PENDING — no live key is available in the environment or `.env`.

## Prediction

The configured cheap default `openai/gpt-4o-mini`, pinned to OpenAI with
`require_parameters=true` and provider fallbacks disabled, will honour a strict
`json_schema` whose allowed record ids change between calls. Two calls with
different record ids will both return the requested member of their own enum.

## Experiment

`uv run docket spike-models` performs the two synthetic calls with a $0.50 run
cap and the persisted daily global cap. It writes the configured model and
provider, the enum values, the measured choices, the call count and the
OpenRouter usage cost to `docs/adr/model-spike-result.json`. No input document or API key is recorded.
The script fails if either output is invalid or differs from the requested id.

## Result

PENDING. No live call has been made, so there is no result file yet. The local
tests use a fake HTTP transport. They check the request shape and how the cost is
read from the usage data. They do not show that a real provider keeps to the enum.
When the experiment passes, `docket spike-models` replaces this section with the
measured result and keeps the JSON file. If it fails, Docket does not switch to
another model or provider on its own.

## Decision

Use a strict JSON schema built for each call, and check the answer again with
Pydantic. If the structured parser returns nothing, Docket parses the raw JSON
against the same schema, so this fallback cannot accept an id outside the enum.
Output that does not fit, including answers the SDK reports as cut off, filtered
or invalid, raises `ParseError`, which is never retried. No other model or provider
has been measured.

Protocol references:
[structured output](https://openrouter.ai/docs/guides/features/structured-outputs),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[usage cost](https://openrouter.ai/docs/api/reference/overview).
