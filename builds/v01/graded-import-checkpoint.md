# Graded corpus results

Both seeds pass at threshold 0.5857142.
The rule is committed as 85669b2. Seed 7 selected the threshold.
Seed 11 selected no settings. The scoring config is unchanged in this round.
No commit, push, review or model call was made by the implementer.

## Inputs and methods

The operator supplied 40 new answers from fresh independent calls.
All 40 identify claude-haiku-5-5 as the simulator.
Each answer passed its saved request schema and target-model check.
The import added 20 recordings per seed with zero answers pending.
The 400 original recording bytes are unchanged.
The archive has 440 recordings: 422 Haiku and 18 Opus.
Each seed uses 211 Haiku recordings and 9 Opus recordings.
The operator reports that Haiku falsely blocked 18 original filler-text requests.
Opus supplied those answers. Their provenance is preserved.

The reports start with the simulated banner and name both simulators and the target model.
These are not OpenRouter measurements. Provider cost remains unknown, stored as null.
The replay runs made zero external model calls.

All 1,244 input-file content hashes match their values before import.
The corpus, supplied answers, saved requests, prompts and scoring config are unchanged.
No request key changed. No answer was written by the implementer.

## Held-out floors

A gold label gives the expected record, category and decision.
Recall is the share of expected filings found correctly.
The held-out test set is seed 11. It was not used to choose settings.

| Seed-11 floor | Result | Status |
| --- | --- | --- |
| Wrong filings = 0 | 0 | PASS |
| Clean automatic filing >= 90% | 9/10, 90% | PASS |
| Injection filings to the injected id = 0 | 0 | PASS |
| Recall >= rules-only baseline | 36/39, 92.31%, equal to baseline | PASS |

Seed 7 also passes every floor: 38/39 recall, 10/10 clean filing and zero wrong filings.
Seed 11 files 36 of 180 documents, a 20% auto-file rate.
It sends 104 to review and rejects 40. Every outcome has a non-empty reason.
Seed 7 files 38, sends 102 to review and rejects 40.

## Curves

Precision is the share of automatic filings that match gold.
Auto-file rate is the share of all documents selected for filing.
The rules-only baseline chooses the highest-scored record without a model.
Its precision/rate curve matches Docket's on each seed.
Each eight-point curve has six distinct precision/rate pairs.
Precision is 100% at every threshold with a filing. It is undefined with no filings.

| Threshold | Seed-7 filings / 180 | Seed-11 filings / 180 | Precision on both |
| --- | ---: | ---: | ---: |
| 0.5500000 | 38, 21.11% | 37, 20.56% | 100% |
| 0.6142857 | 37, 20.56% | 36, 20.00% | 100% |
| 0.6785714 | 34, 18.89% | 33, 18.33% | 100% |
| 0.7428571 | 34, 18.89% | 33, 18.33% | 100% |
| 0.8071429 | 34, 18.89% | 33, 18.33% | 100% |
| 0.8714286 | 31, 17.22% | 30, 16.67% | 100% |
| 0.9357143 | 30, 16.67% | 29, 16.11% | 100% |
| 1.0000000 | 0, 0% | 0, 0% | Undefined |

Raising the threshold reduces the share filed here. It shows no precision gain in this sample.
At the unchanged default, seed 11 sends graded document doc-0176 to review.
Its score is 0.5842105. Its tier is strong, but its score is below 0.5857142.
The threshold was not lowered after seeing that result.

The seed-11 precision interval is 90.36% to 100% at 95% confidence.
Its recall interval is 79.68% to 97.35%.
The reports include Wilson intervals, which express uncertainty in these proportions.

## Categories and model choice

Category accuracy uses the 140 documents per seed that were not rejected.

| Seed | Docket | Rules-only baseline |
| --- | ---: | ---: |
| 7 | 124/140, 88.57% | 132/140, 94.29% |
| 11 | 124/140, 88.57% | 132/140, 94.29% |

The eight category differences per seed are no_record cases.
Their shortlists are empty, so the workflow uses misc without a model call.
The baseline can still read the category from the document header.

The model's choose step changes no filing/review decision and no filed record on either seed.
This run shows no filing gain from model choice.
A review proposal is a suggested record for a document sent to review.
Review proposals differ on 74 documents in seed 7 and 79 in seed 11.
These documents still go to review on both paths.
Category differences are counted separately from choose decisions.

The shadow reports contain 82 disagreements for seed 7 and 87 for seed 11.
All are classified both_correct against gold: both paths send those documents to review.
Their proposed records or categories differ.

The reports are `evals/reports/2026-11-09-seed-7.{md,json}` and
`evals/reports/2026-11-09-seed-11.{md,json}`.
Their corresponding `-shadow.json` files are regenerated.
The README states the measured lack of filing gain from model choice.

## Checks and changes

| P3 check | Status | Evidence |
| --- | --- | --- |
| Workflow happy, duplicate, failure and poison cases | PASS | All workflow tests pass. Each seed has 180 terminal outcomes with reasons. |
| Per-call enums, injection safety and empty-shortlist zero calls | PASS | All saved answers validate against their request schemas. Workflow and gateway tests pass. Both seed injection floors pass. |
| Review approve, dismiss, timeout, duplicate, late and unknown-id cases | PASS | Workflow and HTTP integration tests pass in both full runs. |
| Temporal history replay and deployment patching plan | PASS | Recorded-history replay tests pass. `docs/adr/002-review-workflow-patching.md` remains unchanged. |
| Real failpoints, restart, ingress order, dedup and S3 outage | PASS | All five compose fault cases pass in `./verify.sh --fault`. |
| Observe mode | PASS | Observe workflow tests pass. The database has zero observe-mode filing rows. |
| Seed evaluations and floors | PASS | Both replay CLI commands exit zero and save PASS reports. All held-out floors pass. |
| Curves, baseline, cases, categories and intervals | PASS | Both reports contain the eight-point curves, per-case results and Wilson intervals. |
| Shadow diff | PASS | Both CLI commands exit zero. All 169 disagreements have gold classifications. |
| Replay reproduction | PASS | Both verification commands reproduce the measured seed-11 report with no model calls. |
| Docker build and health | PASS | API, worker and migration images build. Postgres, Temporal, SeaweedFS, API and worker are healthy. |
| Simulation provenance | PASS | All 440 recordings carry validated provenance and unknown provider cost. Reports carry the simulated banner. |

`./verify.sh`: 313 passed in 53.73s, zero skips.
`./verify.sh --fault`: 318 passed in 159.00s, zero skips.
Both scripts exit zero. Ruff, pyright and secret checks pass.
Candidates cover 38/38 branches, scoring 32/32, ledger 16/16 and storage 20/20.
All required branch coverage is 100%.
Command output is in `graded-import-evidence.txt`.
Full local logs are in `.cache/graded-import/`.

The optional gap is covered by the new table in `tests/core/test_shipped_policy.py:18`.
It loads the shipped config directly, without copying or overriding it.
Two rows require a 0.55 two-signal strong score to go to review.
A 0.60 control row requires filing.
Each row checks the raw score, tier, independent signals and decision.

`tests/test_choice_comparison.py` adds comparison and report tests.
They distinguish routing changes, filed records, review proposals and category labels.
They also require aligned document ids and preserve the distinct-point text in reports.
No existing test changed. These two new files add 11 test cases.

`docket/evaluation.py:275` computes the comparison.
The reports state it plainly near the top and count distinct curve points.
`scripts/reproduce_eval.py` also checks the new comparison field.
No replay assertion was removed or relaxed.

## Deviations and open questions

No dependency was added. No P4 work was started.
The threshold, weights, tiers and core rules are unchanged.
There are no open design questions or pending supplied answers.
Real OpenRouter measurements remain unmeasured under the approved simulation decision.
Other filed findings are unchanged.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`. Base: `24093d1`.
API health endpoint: `http://127.0.0.1:8010/healthz`.
