# Recording, importing and replaying evaluation

Run commands from the repository root.
Start services with `docker compose up -d --wait`.
Seed 7 is for tuning.
Seed 11 is held out: it is used only for the final check.
The score threshold is 0.5857142, chosen on seed 7 only.

Evaluation uses observe mode.
This mode checks decisions without filing documents.
It stores raw inputs only and does not wait for review.
Each run gets a fresh run ID.

Docket's figures come from live answers (see [Live Anthropic answers](#live-anthropic-answers)).
Export and import are how the v0.1 simulated answers were made. They work for any
model whose answers an operator supplies.

## Export requests

```sh
uv run docket eval --seed 7 --mode export --budget 0
uv run docket eval --seed 11 --mode export --budget 0
```

Each seed has a request list in `evals/manifests/seed-N.json`.
Export writes an untracked working file in `evals/pending/` only for keys with no saved answer.
The file contains the system prompt, messages and response schema.
A schema lists the allowed fields, types and values.
It also names the activity, prompt version and target model.
The record-choice schema allows only the shortlist IDs and `none`.
Rejected documents and empty shortlists need no model call.

## Supply and import answers

The operator writes an untracked working file at `evals/answers/<key>.json`.
Use this shape with values from that request:

```json
{
  "output": {"record_id": "an-id-from-the-request-enum"},
  "provenance": {
    "simulated_by": "the-actual-simulator-model-id",
    "standing_in_for": "the-request-target-model-id"
  }
}
```

Provenance names the simulator and the target model.
Classification output has a `category` field instead of `record_id`.
The example shows the shape only. It is not an answer to copy.
The simulator must not see expected labels, code or other answers.

```sh
uv run docket eval --seed 7 --mode import --budget 0
uv run docket eval --seed 11 --mode import --budget 0
uv run python scripts/pack_exchange.py
uv run python scripts/pack_exchange.py --check
uv run docket eval --seed 7 --mode replay --budget 0
uv run docket eval --seed 11 --mode replay --budget 0
uv run docket shadow-diff
./verify.sh
./verify.sh --fault
```

`docket eval` compares Docket with the rules-only baseline.
It checks each difference against the expected decision and stores the result in the report.
`docket shadow-diff` saves that comparison as `<report>-shadow.json`.
It reads the newest seed-11 report unless you pass `--report`.
A review decision can be correct even when its proposed record or category differs.
Category accuracy is reported separately.

Import checks all supplied answers before saving any to the provider's archive: for
`openrouter`, `evals/history/simulated/calls.jsonl`.
Replay reads that file. It uses JSON Lines (JSONL): one JSON object per line.
Bad fields, values, request keys or model provenance cause an error.
Missing answers leave the report pending.
A saved answer is never overwritten.
Repack after importing answers, while both working folders are present.
The durable evidence is `evals/history/simulated/exchange.jsonl.gz`.
It keeps the exact request and answer text, file hashes and provenance.
A file hash is a digest used to check file integrity.
The check command compares that evidence with every replay recording.
Replay and verification work without the untracked working folders.

## The v0.1 simulation

[evals/history/simulated/README.md](../evals/history/simulated/README.md) gives the
simulated answer counts and the archive format.
[Decision 004](adr/004-simulated-model-measurements.md) explains how the answers were
made.

## Replay check

The replay check runs through `scripts/reproduce_eval.py`.
It compares seed 11 with the saved report.
It replays under the model and provider that report was measured with.
It checks each document's result, metrics, curve, provenance and shadow diff.
Continuous integration runs this check through `verify.sh`.
A changed archive or scoring config needs new reports.
Tune on seed 7 only. Do not tune on seed 11.

## Live Anthropic answers

Set `ANTHROPIC_API_KEY` in the environment or gitignored `.env`. Never commit it.
The defaults are `DOCKET_MODEL_PROVIDER=anthropic` and `DOCKET_MODEL=claude-haiku-5-5`.
Live answers go to `evals/recordings/anthropic.jsonl`, apart from the simulated ones.
[Operations](operations.md#model-gateway) explains the budgets, retries and unreadable answers.
[evals/recordings/README.md](../evals/recordings/README.md) gives the recorded pass.

The 2026-11-13 pass ran these, with the daily cap raised to $5 for the run
(`DOCKET_DAILY_BUDGET_USD=5`). Seed 11's budget was what was left of $2.00 after seed 7.

```sh
uv run docket spike-models
uv run docket eval --seed 7 --mode record --budget 2.00
uv run docket eval --seed 11 --mode record --budget 1.98
uv run python scripts/pack_exchange.py --live
uv run docket shadow-diff
uv run docket shadow-diff --report evals/reports/2026-11-13-seed-7.json
uv run python scripts/cost_summary.py
uv run docket eval --seed 11 --mode replay --budget 0
```

A record run's report holds the paid run's budget and cost. When a report must be
regenerated later, for new report text say, `scripts/regenerate_live_report.py` replays
the seed with no model call and copies each record run's fields forward. It takes the
record runs' reports, kept in git, in the order the runs happened. The report keeps
`mode: record`, the last budget, `live_run_cost_usd` summed over the runs, and a
`record_runs` list naming where each run's report is kept.

`scripts/cost_summary.py` writes `evals/cost/<date>-claude-haiku-5-5.json`. It rebuilds
each answer's cost from its tokens and [the price snapshot](../config/model_prices.yaml),
and refuses a recording whose saved cost differs.

Record mode accepts only the synthetic corpus.
It sends only requests with no saved answer. A rerun never resends an answered request,
even one that was refused or otherwise unreadable: that answer replays as the same error.
Its report counts `recorded_before_run` and `requests_to_send`.
Each live answer keeps its raw text, tokens, cost, latency and output mode.
Record mode also writes each request to `evals/pending/`.
`pack_exchange.py --live` packs those requests into `evals/recordings/anthropic-exchange.jsonl.gz`,
so the exact request behind each live answer is kept.
`pack_exchange.py --check` also checks that file against the live recordings when either exists.
A report refuses to mix live answers with simulated or OpenRouter answers.
A live report opens with a line naming the model and the date or dates it was measured.

## Workflow histories

```sh
uv run python scripts/record_workflow_history.py
```

This captures happy filing and review approval on the Temporal test server.
These files record workflow events with test activities.
They are not model answer archives.
Temporal Replayer checks them on every verify run.
Keep old histories when workflow commands change.
Decision 002 explains how to keep running workflows working across code changes.
