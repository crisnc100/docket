Both fixes are complete in one change:

- **x1:** Full-strength conflicts are checked across all records before shortlist truncation.
- **x2:** Address tokens preserve letters and hyphens; `1A`, `1`, `12` and `1-2` remain distinct. Abbreviations still match.

Failing-first evidence: **13 failed / 3 controls passed before; all 16 pass after**. No existing tests changed.

Validation: **329 passed** normally; **334 passed** with faults. Zero skips; required branch coverage 100%.

All **440 replay keys are unchanged**. Both seed evals pass. Seed-11 floors remain unchanged: **0 wrong filings, 90% clean filing, 0 injected-ID filings, recall 36/39**, equal to baseline.

[Checkpoint and evidence](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/x1-x2-checkpoint.md). No commits made.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · `v01` · base `499ea40` · [API health](http://127.0.0.1:8010/healthz).