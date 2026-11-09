# Saved model answers

All 400 operator answers passed their saved response schemas.
A schema lists the allowed fields, types and values.
The archive is `calls.jsonl`.
JSON Lines (JSONL) stores one JSON object on each line.
Each entry keeps the answer, request key and model provenance.
Provenance names the simulator and the target model.

The answer mix is 382 from claude-haiku-5-5 and 18 from claude-opus-5-5.
Each seed has 191 Haiku answers and 9 Opus answers.
The operator reports that Haiku's safety filter falsely blocked 18 requests.
Those requests contained synthetic filler text.
The operator reran them on Opus.
Each call saw only its own prompt, messages and schema.
It saw no expected labels, code or other answers.

These are simulated answers, not an OpenRouter measurement.
Provider cost is unknown and stays null.
The original answers remain in `evals/answers/`.
See `docs/rerecord.md` for import and replay commands.

Tests use separate inputs under `tests/`.
They do not supply evaluation results.
The YAML files under `tests/workflow/` are captured Temporal histories.
They record workflow events, not provider answers.
