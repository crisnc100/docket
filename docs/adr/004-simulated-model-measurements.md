# Decision 004: use operator-supplied simulated answers

Status: superseded as the source of Docket's figures by [ADR 008](008-real-model.md).
Kept as history: the simulated answers and their reports are in
[evals/history/simulated/](../../evals/history/simulated/README.md).

## Context

No OpenRouter key is available, but the evaluation needs a model answer for every
request the workflow makes. Docket must not invent those answers itself.

## Decision

Docket exports each request to a file, and the operator supplies the answers.
Claude Haiku answered in place of the target model. Opus answered the requests that
Haiku's safety filter falsely blocked on synthetic filler text. The operator reports
that each answer came from a fresh call that saw only its request and response
schema, and no expected labels, code or other answers. A response schema lists the
allowed fields, types and values. Docket's code made no model call and stored every
supplied answer unchanged.

Import checks every answer against its saved schema before saving any of them. A
record choice can only name a shortlisted id or `none`. A request key binds the
prompt, schema, input and target model, so a different answer cannot replace a
saved one. Each answer's provenance names the model that gave it and the model it
stands in for. The answer counts are in
[evals/history/simulated/README.md](../../evals/history/simulated/README.md).

Reports start with a simulated banner and give their answer counts from saved
provenance, not from the operator's totals. Method and fallback details are marked
as operator reports. Simulated provider cost stays null.

## Result

The filing threshold is 0.5857142. It was chosen on seed 7 from rules-only scores
(`scripts/tune_threshold.py`, evidence in `evals/threshold-selection.json`). Weights
and tier thresholds are untuned defaults. Seed 11 is held out and chose no setting.

Seed 7 and seed 11 both pass the four filing floors at that threshold: no wrong
filings, at least 90% of clean documents filed, no filing to an injected id, and
recall at least equal to the baseline. On seed 11 the model's choose step changes no
filing decision. The reports hold the curves, intervals and category accuracy.

The model only suggests a record to the reviewer. The rules' top score decides the
tier and whether a document files, and the review page shows both picks. On the 102
seed-11 documents sent to review, the model's suggestion was correct on 44 (43.1%)
and the rules' top candidate on 62 (60.8%).

Replay uses saved answers and makes no model calls. The replay check compares the
metrics, each document's result and the model provenance with the saved report.

These results do not measure OpenRouter schema handling, quality or cost. The
real-provider spike and evaluation still need a key, and their answers go in a
separate archive.
