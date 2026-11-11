# Address token slice

Both findings pass. The changes are uncommitted.
No model, review or grade was run.

| Finding | Status | Change and test |
| --- | --- | --- |
| f1: hyphen-joined filenames lose address evidence | PASS | `docket/core/candidates.py:22` splits hyphens between address words. `tests/core/test_address_unit_tokens.py:11` checks the supplied conflict through body text, subject and filename. The raw score remains 0.65, but the conflict forces review at score zero. |
| f2: letter-leading unit identifiers collapse | PASS | `docket/core/candidates.py:11` preserves whole unit identifiers after Apt, Apartment, Unit or #. Line 14 includes letter-leading units in the fuzzy comparison guard. `tests/core/test_address_unit_tokens.py:32` requires zero address support for A1/B1, B1/A1, 1A/1, 12/1-2 and 1/1-2. All remain ID-only review cases. |

Unit identifiers keep letters and internal hyphens. A1, B1, 1A, 12 and 1-2 remain distinct.
Hyphens elsewhere separate address words.
`1010-Cedar-Street-Apt-1.pdf` becomes `1010 cedar st unit 1 pdf`.
The abbreviation controls at `tests/core/test_address_unit_tokens.py:51` cover all five identifiers with Apt., Unit and #.
They retain exact address support and file with an agreeing id.
The tests use shipped records. Lettered units use an in-memory address override with no aliases.
The records file and scoring config are unchanged.

## Failing-first evidence

Command: `uv run pytest tests/core/test_address_unit_tokens.py -q`.
Before the implementation change: `20 failed, 3 passed in 0.13s`.
All 23 cases pass in both full verification runs.
The 16 earlier conflict and address regression cases also pass unchanged.

Direct checks load the committed source from f497aaf and compare it with the worktree:

| Trigger | Committed source | Worktree |
| --- | --- | --- |
| Hyphen-joined filename for UNIT-0001, body for UNIT-0002 | No conflict; score 0.6500000; file | Conflict; score 0; review |
| Configured unit A1, submitted unit B1, agreeing explicit id | Address strength 0.9523809523809522; score 0.5857142; file | Address strength 0; score 0.3000000; medium; review |

Changed existing tests: none. The new file adds 23 cases.
No test or check was deleted, skipped or relaxed.

## Replay and held-out floors

Before either evaluation, all 440 requests matched their saved keys and contents.
Each seed has 220 requests, with zero added, removed or changed.
No request was exported. No answer or recording was written.

Both commands exited zero and printed PASS:

```sh
uv run docket eval --seed 7 --mode replay --budget 0
uv run docket eval --seed 11 --mode replay --budget 0
```

Every report field is unchanged on both seeds.
Both shadow-diff commands printed COMPLETE. All report files match the accepted versions.
All 1,254 corpus, config, request, answer, recording and manifest files are unchanged.
The filing threshold stays at 0.5857142. No setting was selected using seed 11.

No seed-11 floor changed:

| Floor | Result | Status |
| --- | --- | --- |
| Wrong filings = 0 | 0 | PASS |
| Clean filing >= 90% | 9/10 | PASS |
| Filings to injected ids = 0 | 0 | PASS |
| Recall >= rules-only baseline | 36/39, equal to baseline | PASS |

Both eight-point curves still have six distinct precision/rate points.
Category accuracy remains 124/140 on both seeds, versus 132/140 for the baseline.
Model choice changes no filing decision or filed record on either seed.
The reports retain their simulated banner and provenance.
These are simulated measurements, not OpenRouter measurements.

## Verification

| Command or check | Result |
| --- | --- |
| `./verify.sh` | PASS; exit 0; 352 passed in 104.56s; zero skips |
| `./verify.sh --fault` | PASS; exit 0; 357 passed in 214.45s; zero skips |
| Real compose fault tests | Five passed, including failpoint assertions |
| Seed-11 replay reproduction | PASS in both verification commands |
| Branch coverage | Candidates 38/38; scoring 32/32; ledger 16/16; storage 20/20; all 100% |
| Ruff, strict core pyright and Docker builds | PASS |
| Secret scans | PASS; no findings |
| Compose health | api, worker, postgres, temporal and seaweedfs healthy |
| API health | `GET http://127.0.0.1:8010/healthz` returns `{"status":"ok"}` |

Command evidence is in `address-token-evidence.txt`.
Full local logs are in `.cache/slice-address-tokens/`.
Deviations: none. Open questions: none. No dependency was added.
The operator owns the commit and slice review.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`. Change base: `f497aaf`.
