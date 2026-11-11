# Replay after the tenant corrections

Both seed evaluations pass with the four operator-supplied answers.
No external model was called. No commit, push, review or grade was run.

## Held-out seed 11

| Floor | Result | Status |
| --- | --- | --- |
| Wrong filings = 0 | 0 | PASS |
| Clean filing >= 90% | 10/10, 100% | PASS |
| Filings to injected ids = 0 | 0 | PASS |
| Recall >= rules-only baseline | 38/39, 97.44%; equal to baseline | PASS |

Seed 11 files 38 documents, reviews 102 and rejects 40.
Seed 7 files 39, reviews 101 and rejects 40. Its recall is 39/39.
Filing precision is 100% on both seeds.
The threshold remains 0.5857142. No scoring config or policy changed.

Choose changes no filing decision, routing decision or filed record on either seed.
It changes review proposals on 74 documents in seed 7 and 79 in seed 11.
Those documents still go to review. The model shows no filing gain over the baseline.
Category accuracy is 124/140 (88.57%) for Docket and 132/140 (94.29%) for the baseline.
Eight no-candidate documents per seed receive misc without a model call.

Both curves have eight points and six distinct precision/rate pairs.
Precision is 100% at every point with filings. Raising the threshold loses filings here.

| Threshold | Seed-7 filings | Seed-11 filings |
| --- | --- | --- |
| 0.5500000 | 39 | 39 |
| 0.6142857 | 38 | 38 |
| 0.6785714 | 35 | 35 |
| 0.7428571 | 35 | 35 |
| 0.8071429 | 35 | 35 |
| 0.8714286 | 32 | 32 |
| 0.9357143 | 31 | 31 |
| 1.0000000 | 0 | 0 |

The saved filing threshold falls between the first two curve points.
Seed-7 doc-0068 and seed-11 doc-0035 and doc-0045 changed from review to filing.
These are the three documents corrected by the accepted unique-tenant fix.
No other document result changed from the prior reports.

## Answers and provenance

Import validated all four answers against their request schemas and target models.
The supplied mix is three claude-haiku-5-5 answers and one claude-opus-5-5 answer.
The operator reports that Opus answered after a false Haiku safety block.
The original 440 recording bytes remain unchanged. Four entries were appended.

| Scope | Haiku | Opus | Total |
| --- | --- | --- | --- |
| Current seed 7 | 212 | 8 | 220 |
| Current seed 11 | 211 | 9 | 220 |
| Current seeds combined | 423 | 17 | 440 |
| Full archive | 425 | 19 | 444 |

Four archived requests describe earlier document versions and are no longer used.
Two of those answers are Haiku and two are Opus.
The archive retains them for traceability.
Its 19 Opus answers cover 18 original safety blocks and one in the replacement batch.
All measurements remain simulated. No OpenRouter result or cost was invented.

The report banner names each simulator and its count for that seed.
The archive count is labelled separately so unused historical answers are visible.
README links to the current reports and states the lack of filing gain from choose.

Reports: `evals/reports/2026-11-10-seed-{7,11}.{json,md}`.
Shadow reports: `evals/reports/2026-11-10-seed-{7,11}-shadow.json`.
Both shadow commands complete. All 82 seed-7 and 87 seed-11 disagreements are checked against gold.
The earlier reports remain unchanged.

## Guard data and tests

The merged tenant corrections made the original payload guard fail on three PDFs.
Each replacement was checked byte-for-byte against core at 4d493e0 before its hash was updated.
Only those three hashes changed. Every original path remains checked.
`corpus/history/original-digests-pre-core.json` preserves the prior map byte-for-byte.
The new test requires the same 342 paths, exactly those three corrections and the corrected tenant text.

The seed-7 trial counts also changed by one correct filing.
The original selection is preserved in `evals/history/threshold-selection-pre-core.json`.
The current trial table is revalidated using only seed 7.
It selects the same 0.5857142 threshold. No setting was selected using seed 11.
The existing exact trial-table comparison remains intact.
A new test checks the unchanged threshold and the one-document gain at each eligible trial.

Existing test change, for a separate test-only commit:

- `tests/test_pr1_review_compose.py:14`: the expected service set includes the P3 API.
- `tests/test_pr1_review_compose.py:15`: the expected binding count is five instead of four.
- Line 16 is unchanged. Every published port must still bind to 127.0.0.1.

The old expectations failed because the API is present. The updated test passes.
No service or port is filtered out of the security check.
No other existing test changed. No check was removed, skipped or relaxed.
The new report-banner test failed before the banner change and passes after it.

## Verification

`./verify.sh` passes: 412 tests in 110.71s, zero skips.
Candidate branch coverage is 49/50 (98%). Scoring, ledger and storage are 100%.
The held-out replay reproduces. Ruff, strict core pyright, Docker builds and secret checks pass.
`./verify.sh --fault` also passes: 417 tests in 242.10s, zero skips.
All five real compose fault tests pass, including their failpoint assertions.
The held-out replay reproduces in both verification commands.
Both secret scans pass. All five services are healthy, and API health returns `{"status":"ok"}`.

Command evidence is in `reimport-four-evidence.txt`.
Local command logs are in `.cache/reimport-four/`.
No dependency was added. The operator owns the commit and test-change review.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`. Change base: `7a01e0e`.
Local API: `http://127.0.0.1:8010`.
