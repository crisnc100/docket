Fixed f1 and f2 in one uncommitted change. Hyphenated filenames retain address evidence; unit identifiers such as A1, B1 and 1-2 stay distinct. Added 23 failing-first cases. No existing tests changed.

- `./verify.sh`: **352 passed**
- `./verify.sh --fault`: **357 passed**
- Zero skips; 100% required branch coverage.
- Both replay seeds pass; all 440 keys are unchanged.
- Seed-11 floors unchanged: wrong filings **0**, clean filing **90%**, injected-ID filings **0**, recall **36/39**, equal to baseline.

[Checkpoint and evidence](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/address-token-checkpoint.md). No commit made.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`, branch `v01`, base `f497aaf`. [API health](http://127.0.0.1:8010/healthz) passes.