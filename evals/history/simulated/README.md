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
See [docs/rerecord.md](../../../docs/rerecord.md) for import and replay commands.
