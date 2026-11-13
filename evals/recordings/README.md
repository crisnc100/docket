# Saved model answers

`anthropic.jsonl` holds the live answers from claude-haiku-5-5 on the Anthropic API,
recorded on 2026-11-13. Default replay reads it.
JSON Lines (JSONL) stores one JSON object on each line.

One pass over seeds 7 and 11 asked each of their 440 requests once:

| | Seed 7 | Seed 11 | Total |
| --- | --- | --- | --- |
| Requests | 220 | 220 | 440 |
| Answers | 200 | 200 | 400 |
| Unreadable: refusal | 20 | 20 | 40 |
| No answer | 0 | 0 | 0 |
| Spend | $0.0146486 | $0.014665 | $0.0293136 |

Every refusal is on a `very_long` test document, on both the category and the
suggestion call. [Failure 0017](../../docs/failures/0017-very-long-refusal.md) explains
them. Ten seed-7 refusals were recorded before Docket kept the API's refusal category,
so they have no `refusal_category`; the other 30 say `bio`. A diagnostic resend of the
ten, outside this archive, also returned `bio`.

Each entry keeps:

- the request key, which binds the activity, prompt version, model and input;
- the outcome, `answer` or `unreadable`, with the reason for an unreadable one
  (`refusal`, `cut`, `not_json` or `schema`) and, for a refusal, its category;
- the parsed output and the raw answer text;
- input and output tokens, the cost, the latency and the output mode
  (`json_schema` for all 440);
- provenance: `{provider: anthropic, model: claude-haiku-5-5, recorded: <date>}`.

A request with a saved entry is never sent again, even when its answer was unreadable.
Replaying an unreadable entry raises the same error it raised live.

`anthropic-exchange.jsonl.gz` keeps the exact request behind each answer: system
prompt, messages, schema and inputs. It is gzip-compressed JSONL, sorted by request
key, with a fixed gzip timestamp so packing unchanged files gives identical bytes.
Record mode writes each request to the untracked `evals/pending/` folder, and packing
moves them into the archive:

```sh
uv run python scripts/pack_exchange.py --live
uv run python scripts/pack_exchange.py --check
```

The check needs only the archives and the recordings. It checks that every live
recording has its archived request, and it checks the simulated history archive too.
[evals/cost/](../cost/) holds the cost summary rebuilt from these recordings.
See [docs/rerecord.md](../../docs/rerecord.md) for the record and replay commands.

The v0.1 simulated answers are in [evals/history/simulated/](../history/simulated/README.md).

Tests use separate inputs under `tests/`.
They do not supply evaluation results.
The YAML files under `tests/workflow/` are captured Temporal histories.
They record workflow events, not provider answers.
