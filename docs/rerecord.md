# Importing and replaying evaluation

Run commands from the repository root.
Start services with `docker compose up -d --wait`.
Seed 7 is for tuning.
Seed 11 is held out: it is used only for the final check.
The score threshold is 0.5857142, chosen on seed 7 only.

Evaluation uses observe mode.
This mode checks decisions without filing documents.
It stores raw inputs only and does not wait for review.
Each run gets a fresh run ID.

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

Shadow diff reads the newest seed-11 report unless you pass `--report`.
It compares Docket with the rules-only baseline.
It checks each difference against the expected decision.
A review decision can be correct even when its proposed record or category differs.
Category accuracy is reported separately.

Import checks all supplied answers before saving any to `evals/recordings/calls.jsonl`.
Replay reads that file. It uses JSON Lines (JSONL): one JSON object per line.
Bad fields, values, request keys or model provenance cause an error.
Missing answers leave the report pending.
A saved answer is never overwritten.
Repack after importing answers, while both working folders are present.
The durable evidence is `evals/recordings/exchange.jsonl.gz`.
It keeps the exact request and answer text, file hashes and provenance.
A file hash is a digest used to check file integrity.
The check command compares that evidence with every replay recording.
Replay and verification work without the untracked working folders.

## Current simulation

[evals/recordings/README.md](../evals/recordings/README.md) gives the current answer
counts and the archive format.
[Decision 004](adr/004-simulated-model-measurements.md) explains how the answers were
made.

## Replay check

The replay check runs through `scripts/reproduce_eval.py`.
It compares seed 11 with the saved report.
It checks each document's result, metrics, curve, provenance and shadow diff.
Continuous integration runs this check through `verify.sh`.
A changed archive or scoring config needs new reports.
Tune on seed 7 only. Do not tune on seed 11.

## Real OpenRouter measurements

Set OPENROUTER_API_KEY in the environment or gitignored `.env`.
Never commit it.
Use a separate archive so real and simulated answers cannot mix.
Per-run and daily budget limits still apply.

```sh
uv run docket spike-models
DOCKET_REPLAY_PATH=evals/recordings/openrouter.jsonl uv run docket eval --seed 11 --mode record --budget 2.00
DOCKET_REPLAY_PATH=evals/recordings/openrouter.jsonl uv run docket eval --seed 11 --mode replay --budget 0
```

Real measurements keep provider usage cost.
Record mode accepts only the synthetic corpus.
Do not label simulated results as real-provider results.

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
