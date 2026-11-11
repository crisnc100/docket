# Graded corpus export checkpoint

Status: ready for the operator's answers. No commit was attempted.
The 40 new requests are the only unanswered requests.

| Seed | Added documents | Classify requests | Choose requests | Pending answers |
| --- | ---: | ---: | ---: | ---: |
| 7 | 10 | 10 | 10 | 20 |
| 11 | 10 | 10 | 10 | 20 |

The new-key lists are `evals/manifests/graded-seed-7.json` and
`evals/manifests/graded-seed-11.json`.
Requests are in `evals/pending/<key>.json`.
Answers belong in `evals/answers/<key>.json`, with the existing answer shape.
No new answer or recording was written.

## Findings and evidence

| Check | Status | Evidence |
| --- | --- | --- |
| f4: graded strengths | PASS | `docket/core/candidates.py:50` grades current and former names. Line 60 grades addresses. Exact ids stay at one. Continuous-field tests are in `tests/test_graded_evidence.py`. |
| f4: deterministic additions | PASS | `docket/corpus.py:99` adds ten fixed profiles per seed. Both seeds have 180 documents. Existing and new determinism tests pass. |
| f4: original corpus and requests | PASS | All 340 original payloads and label prefixes match saved hashes. All 400 original request keys and saved requests match. All original answers and recordings are unchanged. |
| f4: seed-7 curve | PASS | The eight-point rules-only curve has six distinct precision/rate points. `test_seed7_curve_has_at_least_three_distinct_points_and_reproduces_selection` requires at least three. |
| f4: threshold choice | PASS | `scripts/tune_threshold.py:12` implements the saved selection rule. `evals/threshold-selection.json` records seed 7 and threshold 0.5857142. |
| f4: new model comparison and held-out results | PENDING-SIMULATION | Each seed lacks 20 answers. No new model comparison, held-out curve or category accuracy is claimed. |
| f3: response-source labels | PASS | `docket/evaluation.py:329` distinguishes no responses, simulated responses, provider responses and mixed sources. Report and shadow-label tests pass. |
| Full normal verification | PASS | `./verify.sh`: 302 passed, zero skips. |
| Full fault verification | PASS | `./verify.sh --fault`: 307 passed, zero skips. All five real compose fault tests pass. |
| Coverage | PASS | Candidates 38/38, scoring 32/32, ledger 16/16 and storage 20/20 branches. Each is 100%. |
| Lint, types, Docker and secrets | PASS | Both verification commands exit zero. Ruff, pyright, Docker builds and secret checks pass. All five services are healthy. |
| Replay check | PASS for pending state | The unchanged replay script reproduces PENDING-SIMULATION. Reproduction of the new measured report awaits answers. |

Command output is saved in `graded-export-evidence.txt`.
The normal and fault logs are `.cache/graded/verify.log` and `.cache/graded/fault.log`.

The seed-7 threshold comes only from rules-only scores.
The rule requires zero wrong filings and at least 90% clean automatic filing.
It maximizes correct filings and breaks ties with the highest threshold.
At 0.5857142, it selects 38 correct filings with zero wrong filings.
All ten clean documents are selected.
Weights, tier thresholds and fuzzy cutoffs remain untuned defaults.
The two-signal rule, conflict rule and strong-tier filing gate are unchanged.

Precision is the share of automatic filings that match the expected label.
Auto-file rate is the share of all documents selected for filing.
This curve measures the rules-only baseline, without model answers.

| Threshold | Precision | Auto-file count | Auto-file rate |
| --- | ---: | ---: | ---: |
| 0.5500000 | 1 | 38 | 38/180 |
| 0.6142857 | 1 | 37 | 37/180 |
| 0.6785714 | 1 | 34 | 34/180 |
| 0.7428571 | 1 | 34 | 34/180 |
| 0.8071429 | 1 | 34 | 34/180 |
| 0.8714286 | 1 | 31 | 31/180 |
| 0.9357143 | 1 | 30 | 30/180 |
| 1.0000000 | Undefined | 0 | 0/180 |

Raising the threshold loses coverage here. It shows no precision gain in this sample.
The effect of the model's choice on the expanded corpus is still pending.

## Existing tests changed

1. `tests/test_corpus.py:121`, `test_corpus_cli_generates_labels`.
   The stdout assertion and exact label-count assertion change from 170 to 180.
   They still require the CLI to report and produce the full corpus.
2. `tests/core/test_scoring.py:54`, `test_requested_score_table_from_real_candidate_evidence`.
   The table uses the minimum valid filing threshold, equal to the strong threshold.
   Every expected score, raw score, tier, decision and independent-signal count is unchanged.
   The new production-threshold test separately requires a strong score of 0.55
   to go to review below the selected default threshold.
3. `tests/core/test_scoring.py:247`, the config-policy test.
   Its name and calibration assertion identify the seed-7 threshold selection.
   Exact weight assertions and the 0.25/0.55 tier assertions remain.
   The filing threshold must equal the saved seed-7 selection.
   The new selection test independently reproduces that choice from the corpus.

No other existing test changed. No test was deleted, skipped or narrowed.
The verification and coverage scripts are unchanged.

## Deliberate limits and next step

The shared rule is `graded-corpus-rule.md`. Its content hash is saved with the selection.
The operator must commit that rule before the new seed-11 measurement.
The implementer has not measured the new seed-11 results or used them to select settings.
Exports and pending replay checks return before measuring either expanded seed.

Names with reversed word order and standard address abbreviations remain exact matches.
Typos receive fractional strength. A missing unit number supplies no address support.
Nickname relationships are not inferred.
Candidate admission and ordering stay unchanged to preserve every original request key.
Threshold curves can include a strong review outcome when sweeping below the run threshold.
They still exclude medium-tier and conflicted outcomes.

The current reports mark the expanded runs pending.
Earlier 170-document measurements remain in the accepted base commit.
The reports do not present those earlier numbers as results for 180 documents.
No dependency was added. No model was called. No P4 work was started.
Findings f1, f2 and f5 remain filed. No open design question remains.

After the operator supplies the answers and commits the rule, run:

```sh
uv run docket eval --seed 7 --mode import --budget 0
uv run docket eval --seed 11 --mode import --budget 0
```

Keep the selected threshold fixed when measuring seed 11.
Then run shadow-diff, replay reproduction and both verification commands.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`. Accepted base: `6b9d9b4`.
API health endpoint: `http://127.0.0.1:8010/healthz`.
