P3 passes under the approved simulation policy.

- `./verify.sh`: **276 passed**
- `./verify.sh --fault`: **281 passed**
- Zero skips, required branch coverage 100%, services healthy.
- Both seeds pass import, evaluation and replay. **P3.1–P3.9 are PASS**, with individual evidence in the [checkpoint](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/p3-checkpoint.md).

Reports carry the simulated banner: **382 answers from claude-haiku-5-5 and 18 from claude-opus-5-5**. The operator reran 18 false safety blocks on synthetic filler text through Opus. All 400 answers retain their provenance.

Held-out seed 11, at threshold **0.55**:

| Floor | Measured | Result |
|---|---:|---|
| Wrong files = 0 | 0 | PASS |
| Clean auto-file rate ≥90% | 9/10 = 90% | PASS |
| Filings to injected IDs = 0 | 0 | PASS |
| Recall ≥ baseline | 93.33% = 93.33% | PASS |

Docket category accuracy is **114/130 = 87.69%**, versus **93.85%** for the baseline. The gap comes from `no_record` documents assigned `misc` without a model call.

The filing curve is identical for Docket and the baseline:

| Threshold | Precision | Recall | Auto-file rate |
|---:|---:|---:|---:|
| 0.5500000 | 100% | 93.33% | 16.47% |
| 0.6142857 | 100% | 93.33% | 16.47% |
| 0.6785714 | 100% | 93.33% | 16.47% |
| 0.7428571 | 100% | 93.33% | 16.47% |
| 0.8071429 | 100% | 93.33% | 16.47% |
| 0.8714286 | 100% | 93.33% | 16.47% |
| 0.9357143 | 100% | 93.33% | 16.47% |
| 1.0000000 | Undefined | 0% | 0% |

All 170 documents ended with a reason: **28 would file, 102 would review, 40 rejected**. The shadow report classifies all 87 differences as correct review routing on both sides.

[Seed 11 report, including intervals](/Users/cortega/Documents/Projects/docket/docket-v01/evals/reports/2026-11-09-seed-11.md) · [Shadow report](/Users/cortega/Documents/Projects/docket/docket-v01/evals/reports/2026-11-09-seed-11-shadow.json)

No commits or model calls were made by me.