# Simulated model answers (v0.1 history)

These are the v0.1 answers, kept unchanged as history. No current figure uses them.
Since v0.2, Docket's figures come from live answers in
[evals/recordings/](../../recordings/README.md), measured on the Anthropic API.
The reports in `reports/` are the v0.1 evaluation reports from 2026-11-09 to
2026-11-11. Each keeps its own banner saying the answers were simulated.
Replay with `DOCKET_MODEL_PROVIDER=openrouter` and `DOCKET_MODEL=openai/gpt-4o-mini`
reads `calls.jsonl` here, because those are the only answers keyed to that model.

All 444 operator answers passed their saved response schemas.
A schema lists the allowed fields, types and values.
Replay reads `calls.jsonl`.
JSON Lines (JSONL) stores one JSON object on each line.
Each entry keeps the answer, request key and model provenance.
Provenance names the simulator and the target model.

The saved answer mix is 425 from claude-haiku-5-5 and 19 from claude-opus-5-5.
Current seed requests use 440 answers. Four older answers remain as evidence.
The operator reports that Haiku's safety filter falsely blocked 19 requests.
Those requests contained synthetic filler text.
The operator reran them on Opus.
Each call saw only its own prompt, messages and schema.
It saw no expected labels, code or other answers.

These are simulated answers, not an OpenRouter measurement.
Provider cost is unknown and stays null.
`exchange.jsonl.gz` is the durable record of all 444 request and answer pairs.
It is gzip-compressed JSONL, sorted by request key.
`request_json` and `answer_json` hold the original UTF-8 file text.
Encoding that text as UTF-8 restores the exact file bytes.
Each entry includes both file SHA-256 hashes and the answer provenance.
SHA-256 is a digest used to check file integrity.
The gzip timestamp is fixed, so packing unchanged files gives identical bytes.
`evals/pending/` and `evals/answers/` are untracked working folders.
They are needed for export and import, but not replay or verification.

```sh
uv run python scripts/pack_exchange.py
uv run python scripts/pack_exchange.py --check
```

Pack after importing answers, while both working folders are present.
Packing merges working pairs into the saved archive and keeps all saved keys.
Answers must match their replay recordings before the archive is replaced.
The check command needs only the archive and `calls.jsonl`.
It checks file hashes, request keys, answer schemas and provenance.
It also checks that every recording has the same archived answer and provenance.
[How the answers were made](#how-v01s-simulated-answers-were-made) gives the commands.

## How v0.1's simulated answers were made

**Historical.** This is the recipe that produced the answers above. The live archive in
[evals/recordings/](../../recordings/README.md) supersedes it. It is not runnable in a
checkout that holds the live evidence: each `docket eval` rewrites the committed seed
manifests for the simulated model, and a replay writes a report for today's date, which
breaks the checks that pin the live manifests and reports.

Each `docket eval` command sets the simulated provider and model for that command only,
so answers go to `calls.jsonl` here and not to the live archive.

### Export requests

```sh
DOCKET_MODEL_PROVIDER=openrouter DOCKET_MODEL=openai/gpt-4o-mini \
  uv run docket eval --seed 7 --mode export --budget 0
DOCKET_MODEL_PROVIDER=openrouter DOCKET_MODEL=openai/gpt-4o-mini \
  uv run docket eval --seed 11 --mode export --budget 0
```

Each seed has a request list in `evals/manifests/seed-N.json`.
Export writes an untracked working file in `evals/pending/` only for keys with no saved answer.
The file contains the system prompt, messages and response schema.
A schema lists the allowed fields, types and values.
It also names the activity, prompt version and target model.
The record-choice schema allows only the shortlist IDs and `none`.
Rejected documents and empty shortlists need no model call.

### Supply and import answers

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
DOCKET_MODEL_PROVIDER=openrouter DOCKET_MODEL=openai/gpt-4o-mini \
  uv run docket eval --seed 7 --mode import --budget 0
DOCKET_MODEL_PROVIDER=openrouter DOCKET_MODEL=openai/gpt-4o-mini \
  uv run docket eval --seed 11 --mode import --budget 0
uv run python scripts/pack_exchange.py
uv run python scripts/pack_exchange.py --check
DOCKET_MODEL_PROVIDER=openrouter DOCKET_MODEL=openai/gpt-4o-mini \
  uv run docket eval --seed 7 --mode replay --budget 0
DOCKET_MODEL_PROVIDER=openrouter DOCKET_MODEL=openai/gpt-4o-mini \
  uv run docket eval --seed 11 --mode replay --budget 0
uv run docket shadow-diff
./verify.sh
./verify.sh --fault
```

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
