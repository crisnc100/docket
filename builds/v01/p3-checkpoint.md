Model responses simulated by claude-haiku-5-5, standing in for openai/gpt-4o-mini; not an OpenRouter measurement.
Model responses simulated by claude-opus-5-5, standing in for openai/gpt-4o-mini; not an OpenRouter measurement.

# P3 checkpoint — PASS under the simulation decision

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`. Base: `942ba4c`. API: http://127.0.0.1:8010.
No commit, push, PR, review or model call was made by the implementer.

A simulator supplies model answers. A gold label gives the expected result.
An enum lists the allowed response values.
A schema sets the response fields and types.
A workflow records the document steps.
Provenance names the answering model and its target.
Both seeds used saved operator answers. No scoring value changed.
The approved score threshold stays at 0.55.

## P3 exit checks

| Check | Result | Evidence |
| --- | --- | --- |
| P3.1 workflow and bounded retries | PASS | Storage-key inputs and seven activities. The workflow tests cover all required paths. Non-retryable errors run once. `./verify.sh`: 276 passed. |
| P3.2 category response | PASS | Enum and unknown-to-misc tests pass. All imported classification answers pass their saved schema. |
| P3.3 shortlist-only choice | PASS | Per-call enum tests pass. Empty-shortlist tests prove zero model calls. All imported choices pass the request enum. |
| P3.4 review routing | PASS | Tests cover approve, dismiss, expiry, unknown IDs and late actions. Concurrent double approval returns 200 and 409. One filing remains. |
| P3.5 workflow replay | PASS | Temporal Replayer passes both saved histories. They cover filing and an opened review approved by update. Decision 002 explains future code changes. |
| P3.6 failpoints | PASS | Each of five fault tests checks its fired log and exit code 137. See `p3-fault-evidence.txt`. |
| P3.7 fault suite | PASS | `./verify.sh --fault`: 281 passed. Real API and worker crashes recover with one object, row and filed event. A real SeaweedFS outage is retried. |
| P3.8 evaluation and replay | PASS | Both imports and both replay evaluations exit 0. Both seeds pass all four floors. Seed 11 reproduces through both verify commands. The archive has 400 validated answers. |
| P3.8 simulated schema check | PASS | All 400 answers pass the saved response schemas. The target OpenRouter model itself has not been tested. |
| P3.9 shadow diff | PASS | Seed 7 has 82 differences. Seed 11 has 87. Every difference is classified against gold. All have correct review routing on both sides. |
| Terminal results | PASS | Each seed has 170 terminal results with a reason. Seed 11 has 28 would_file, 102 would_review and 40 rejected. No failed result. |
| Observe mode | PASS | Observe mode predicts decisions without filing. The database has zero observe filing rows. The default S3 bucket has zero objects outside raw/. |
| Build and health | PASS | Both verify commands build the app images. Postgres, Temporal, SeaweedFS, API and worker report healthy. |
| Fixed stack and checks | PASS | No dependency or lockfile changed. Ruff and pyright pass. Required branch coverage is 100%. Zero skipped tests. Secret checks pass. |

Real OpenRouter schema, quality and cost checks remain deferred.
The approved plan replaces those checks with simulation for this phase.
Reports and recordings remain uncommitted, as instructed.
Remote continuous integration has not run because nothing was pushed.

## Simulator mix

The archive confirms 382 answers from claude-haiku-5-5 and 18 from claude-opus-5-5.
Each seed has 191 Haiku answers and 9 Opus answers.
All 400 unique saved answers were used.
The operator reports false safety blocks on 18 synthetic filler text requests.
Those requests were rerun on Opus. Each answer retains the actual model name.

The operator says each answer came from a fresh call.
It saw only its own system prompt, messages and response schema.
It saw no expected labels, code or other answers.
This method is an operator report. Counts were checked from saved answers.
Provider cost stays null. No usage cost was invented.

## Held-out seed 11

Held out means these documents were not used to tune the score.
Precision is the share of automatic filings that match gold.
Recall is the share of expected filings found correctly.
Auto-file rate uses all 170 documents.

| Floor at 0.55 | Measured | Result |
| --- | --- | --- |
| Wrong files = 0 | 0 of 28 automatic filings | PASS |
| Clean auto-file rate >= 90% | 9/10 = 90% | PASS |
| Filings to injected IDs = 0 | 0 | PASS |
| Recall >= rules-only baseline | 28/30 = 93.33%; baseline 28/30 = 93.33% | PASS |

Automatic filing precision is 100%. Auto-file rate is 16.47%.
Review rate is 60%. Forty documents are rejected.
Category accuracy is 114/130 = 87.69%. The baseline gets 122/130 = 93.85%.
Rejected documents are excluded from category accuracy.
The gap comes from no_record cases: Docket gets 2/10 right, versus 10/10.
With no candidates, Docket makes no model call and assigns misc.

Wilson intervals show uncertainty at 95% confidence.
Precision: 87.94–100%. Recall: 78.68–98.15%.
Category accuracy: 80.94–92.28%.

## Held-out curve

The rules-only baseline has the same filing curve.
All 28 selected filings score 0.95.
A dash means there are no automatic filings, so precision is undefined.

| Threshold | Filings | Precision | Recall | Auto-file rate |
| --- | ---: | ---: | ---: | ---: |
| 0.5500000 | 28 | 100.00% | 93.33% | 16.47% |
| 0.6142857 | 28 | 100.00% | 93.33% | 16.47% |
| 0.6785714 | 28 | 100.00% | 93.33% | 16.47% |
| 0.7428571 | 28 | 100.00% | 93.33% | 16.47% |
| 0.8071429 | 28 | 100.00% | 93.33% | 16.47% |
| 0.8714286 | 28 | 100.00% | 93.33% | 16.47% |
| 0.9357143 | 28 | 100.00% | 93.33% | 16.47% |
| 1.0000000 | 0 | — | 0.00% | 0.00% |

Seed 7 also passes all floors. It has 29 correct filings and 100% clean filing.
Its recall is 96.67%, equal to its baseline.
No threshold was changed after seeing seed 11.

## Shadow results

A shadow diff compares Docket with the rules-only baseline.
Both-correct means both made the gold routing decision.
Review proposals can still name different records or categories.
That is why these results do not imply equal category accuracy.
Seed 11 has 87 differences, all both_correct. Seed 7 has 82, all both_correct.
The JSON reports retain every difference and its gold label.

## Command evidence

```text
$ uv run docket eval --seed 7 --mode import --budget 0
PASS: evals/reports/2026-11-09-seed-7.json
[exit 0]
$ uv run docket eval --seed 11 --mode import --budget 0
PASS: evals/reports/2026-11-09-seed-11.json
[exit 0]
$ uv run docket eval --seed 7 --mode replay --budget 0
PASS: evals/reports/2026-11-09-seed-7.json
[exit 0]
$ uv run docket eval --seed 11 --mode replay --budget 0
PASS: evals/reports/2026-11-09-seed-11.json
[exit 0]
$ uv run docket shadow-diff --report evals/reports/2026-11-09-seed-7.json
COMPLETE: evals/reports/2026-11-09-seed-7-shadow.json
[exit 0]
$ uv run docket shadow-diff --report evals/reports/2026-11-09-seed-11.json
COMPLETE: evals/reports/2026-11-09-seed-11-shadow.json
[exit 0]
$ ./verify.sh
94 files already formatted
0 errors, 0 warnings, 0 informations
============================= 276 passed in 51.06s =============================
PASS docket/core/candidates.py: 20/20 branches = 100.00% (required 95%)
PASS docket/core/scoring.py: 32/32 branches = 100.00% (required 95%)
PASS docket/core/ledger.py: 16/16 branches = 100.00% (required 95%)
PASS docket/core/storage.py: 20/20 branches = 100.00% (required 90%)
Replay report reproduced: PASS; seed=11; no model calls
detect-secrets: {}
Verified public request SHA-256 digests: 400; all other findings remain blocking
[exit 0]
$ ./verify.sh --fault
94 files already formatted
0 errors, 0 warnings, 0 informations
======================= 281 passed in 165.34s (0:02:45) ========================
PASS docket/core/candidates.py: 20/20 branches = 100.00% (required 95%)
PASS docket/core/scoring.py: 32/32 branches = 100.00% (required 95%)
PASS docket/core/ledger.py: 16/16 branches = 100.00% (required 95%)
PASS docket/core/storage.py: 20/20 branches = 100.00% (required 90%)
Replay report reproduced: PASS; seed=11; no model calls
detect-secrets: {}
Verified public request SHA-256 digests: 400; all other findings remain blocking
[exit 0]
```

Corrupt PDFs log a rejection error during replay.
They end as rejected with reason corrupt. Those log lines are expected.

## Files and test changes

Reports: `evals/reports/2026-11-09-seed-7` and `2026-11-09-seed-11`, in JSON and Markdown.
Each has eight curve points, 17 case types, intervals and model counts.
Each shadow report ends in `-shadow.json`.
The 400 saved responses are in `evals/recordings/calls.jsonl`.
Original answers remain unchanged in `evals/answers/`.
Extra data checks are in `builds/v01/p3-simulation-evidence.txt`.

No existing test changed in this round.
New file `tests/test_simulation_report.py` checks counts from saved answers.
It ignores context for another archive and checks the banner and null cost.
The replay gate now also checks each document result and model provenance.
No test, score, label, skip rule or coverage floor was weakened.
Both reports show zero missing requests.
No implementation question remains. The operator owns the commit.
