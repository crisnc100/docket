# Decision 004: use operator-supplied simulated answers

Status: imported and measured. Real-provider checks are deferred.

The operator supplied 400 answers.
Each answer came from a fresh call with only its request and response schema.
A schema lists the allowed fields, types and values.
The operator reports that no call saw expected labels, code or other answers.
The implementer did not invoke a model or write an answer.

Saved provenance confirms 382 Haiku answers and 18 Opus answers.
Provenance names the simulator and the target model.
Each seed has 191 answers from claude-haiku-5-5 and 9 from claude-opus-5-5.
The operator reports false safety blocks on 18 synthetic filler text requests.
Those requests were rerun on Opus.
The archive keeps each answer's actual model identity.

Import checked every answer against its saved schema.
The record choice could only name a shortlisted ID or `none`.
All 400 passed. Import kept the original answers unchanged.
A request key binds its prompt, schema, input and target model.
Different answers cannot replace an existing saved entry.

Reports start with the required simulated banner.
They list counts for the seed, workflow use and the whole archive.
Counts come from saved answers, not the operator's stated totals.
Method and fallback details are marked as operator reports.
Simulated provider cost stays null.

Seed 7 and seed 11 pass the four filing floors at score 0.55.
The reports contain the measured curves, intervals and category accuracy.
No scoring value changed for this run.
Replay uses saved answers and makes no model calls.
The replay check compares metrics, each document's result and model provenance.

These results do not measure OpenRouter schema handling, quality or cost.
The real-provider spike and evaluation still need a key.
Use a separate archive for those measurements.
