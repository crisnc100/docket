# Recording and replaying evaluation

Run commands from the repository root.
Start services with `docker compose up -d --wait`.
Seed 7 is for tuning.
Seed 11 is held out: it is used only for the final check.
The score threshold is 0.5857142, chosen on seed 7 only.

Evaluation uses observe mode.
This mode checks decisions without filing documents.
It stores raw inputs only and does not wait for review.
Each run gets a fresh run ID.

Docket's figures come from live answers, recorded as below.
How v0.1's simulated answers were made is kept as history in
[evals/history/simulated/README.md](../evals/history/simulated/README.md); it is not
runnable in a checkout that holds the live evidence.

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
uv run python scripts/reproduce_eval.py
```

`scripts/reproduce_eval.py` is the [replay check](#replay-check).

A record run's report holds the paid run's budget and cost. A plain
`docket eval --mode replay` on the same date would replace it, so `write_report` refuses
to overwrite a record run's report with one that is not. When a report must be
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

`docket eval` compares Docket with the rules-only baseline.
It checks each difference against the expected decision and stores the result in the report.
`docket shadow-diff` saves that comparison as `<report>-shadow.json`.
It reads the newest seed-11 report unless you pass `--report`.
A review decision can be correct even when its proposed record or category differs.
Category accuracy is reported separately.

## Replay check

The replay check runs through `scripts/reproduce_eval.py`.
It replays seed 11 under the model and provider of the newest saved seed-11 report, and
compares each document's result, metrics, curve, provenance, shadow diff and live
measures with that report.
It makes no model call and needs no key, but it is not read-only: it uses the local
services (Postgres, Temporal and S3 storage), which get the replayed documents and their
events, and it rewrites `evals/manifests/seed-11.json`. For the committed evidence the
rewrite has the same content, so `git diff` stays empty. It does not write a report.
Continuous integration runs this check through `verify.sh`.
A changed archive or scoring config needs new reports.
Tune on seed 7 only. Do not tune on seed 11.

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
