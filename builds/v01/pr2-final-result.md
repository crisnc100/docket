Both replay evals, shadow diffs and verification commands pass.

Seed-11 floors:

| Check | Result |
|---|---|
| Wrong filings | **0 — PASS** |
| Clean filing | **100% — PASS** |
| Filings to injected IDs | **0 — PASS** |
| Recall ≥ baseline | **38/39, equal — PASS** |

**Choose changes no filing decision or filed record on either seed.**

- `./verify.sh`: **412 passed**
- `./verify.sh --fault`: **417 passed**
- Zero skips; required coverage passes.
- Current simulator mix: **423 Haiku / 17 Opus**. Archive: **425/19**, including four old requests. Banners are updated.

Changed existing test: [test_pr1_review_compose.py:14](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr1_review_compose.py:14) now expects the API and five bindings. Every port must still bind to localhost.

[Seed-7 report](/Users/cortega/Documents/Projects/docket/docket-v01/evals/reports/2026-11-10-seed-7.md) · [Seed-11 report](/Users/cortega/Documents/Projects/docket/docket-v01/evals/reports/2026-11-10-seed-11.md) · [Checkpoint](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/reimport-four-checkpoint.md)

Threshold unchanged; historical evidence preserved. No commit made.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · `v01` · base `7a01e0e` · [API](http://127.0.0.1:8010).