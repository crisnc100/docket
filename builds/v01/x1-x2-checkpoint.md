# PR2 conflict and address fix

Both fixes are in one worktree change. No commit or review was run.
The full normal and fault verification commands pass.

## Findings

| Finding | Status | Code and proof |
| --- | --- | --- |
| x1: full-strength matches bypass conflicts | PASS | `docket/core/candidates.py:160` grades every record before the shortlist is truncated. Lines 170–175 add full-strength name and address matches to the same identity set as explicit ids. The new tests cover current and former names, explicit ids, shortlist sizes one and five, and a contradictory record outside the selected candidates. |
| x2: address comparison drops letters | PASS | `docket/core/candidates.py:11` matches whole identifiers with letters and hyphens. Line 19 preserves hyphens during normalization. Line 68 compares those full tokens before fuzzy address support is allowed. The tests distinguish 1A from 1, 1-2 from both 1 and 12, and 1010A from 1010. Abbreviation controls still file. |

A full-strength signal is 1.0. That includes exact names after word-order normalization.
The conflict check includes every configured record, even one outside the returned shortlist.
The fuzzy cutoff remains 0.88. Weights, tier thresholds and the filing threshold are unchanged.
Input address lines are normalized once for the shared record scan.
Candidate selection keeps its existing priority.

The supplied two-tenant example had raw score 0.65 and was selected for filing.
It has the same raw score with the fix, but the conflict flag forces review at score zero.
The explicit-id tests require that raw score to exceed the shipped filing threshold.
This proves that the conflict override affects the decision.

Unit 1A previously gave address strength 0.975609756097561 for unit 1.
With the explicit id, that produced score 0.5926829 and a filing decision.
It supplies zero address support with the fix. The ID-only score is 0.30 and goes to review.
Unit 1-2 previously matched unit 1 exactly. It also supplies zero address support with the fix.

## Failing-first tests

The new file is `tests/core/test_conflict_address_regressions.py`.
It uses the shipped records and unmodified scoring config.

| Test | Before | After |
| --- | --- | --- |
| `test_x1_full_strength_names_conflict_across_all_shipped_records` | Eight cases failed | Eight passed |
| `test_x1_explicit_id_conflicts_with_full_name_outside_selected_candidates` | Failed | Passed |
| `test_x2_shipped_unit_one_cannot_gain_address_support_from_another_unit` | Two cases failed | Two passed |
| `test_x2_range_one_two_is_not_shipped_unit_twelve_or_unit_one` | Failed | Passed |
| `test_x2_shipped_house_number_suffix_does_not_match_numeric_house` | Failed | Passed |
| `test_x2_shipped_abbreviations_keep_exact_address_support` | Three controls passed | Three passed |

Before changing implementation: 13 failed, 3 passed in 0.14s.
After the change: all 16 passed in 0.14s.
No existing test changed. No test or check was deleted, skipped or relaxed.

## Replay keys and outcomes

All 440 requests were checked before either evaluation ran.
Each seed has 220 unchanged requests, with zero added, removed or modified keys.
Each generated request also matches the saved prompt, input, model and response schema.
No request was exported. No answer or recording was written.

Both commands exit zero:

```sh
uv run docket eval --seed 7 --mode replay --budget 0
uv run docket eval --seed 11 --mode replay --budget 0
```

The saved per-document results, metrics, curves, choice comparison and shadow data are unchanged.
The Markdown reports were regenerated from the saved JSON to preserve their stable key order.
The shadow CLI commands also complete for both seeds.
All report files match the accepted versions.

No seed-11 floor result changed:

| Floor | Before | After | Status |
| --- | --- | --- | --- |
| Wrong filings = 0 | 0 | 0 | PASS |
| Clean filing >= 90% | 9/10 | 9/10 | PASS |
| Filings to injected ids = 0 | 0 | 0 | PASS |
| Recall >= rules-only baseline | 36/39, equal to baseline | 36/39, equal to baseline | PASS |

Seed 7 still files 38 documents. Seed 11 still files 36.
The filing threshold stays at 0.5857142. No seed-11 setting was selected.
These remain simulated measurements. No external model was called.

## Validation

| Command or check | Result |
| --- | --- |
| `./verify.sh` | PASS: 329 passed in 87.82s, zero skips |
| `./verify.sh --fault` | PASS: 334 passed in 195.23s, zero skips |
| Real compose fault tests | All five pass |
| Measured seed-11 replay reproduction | PASS in both verification commands |
| Candidate branch coverage | 36/36, 100% |
| Scoring branch coverage | 32/32, 100% |
| Ledger branch coverage | 16/16, 100% |
| Storage branch coverage against SeaweedFS | 20/20, 100% |
| Ruff and strict core pyright | PASS |
| Docker builds and service health | PASS; all five services healthy |
| Secret check | PASS; no findings |
| Input integrity | All 1,254 saved input hashes unchanged |

Command output and before/after examples are in `x1-x2-evidence.txt`.
Full local logs are in `.cache/x1-x2/`.
The scoring policy, prompts, corpus, replay data and verification scripts are unchanged.
No dependency was added. There are no open design questions.
The operator owns the commit and slice review.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`. Base: `499ea40`.
API health endpoint: `http://127.0.0.1:8010/healthz`.
