PR4 is complete and uncommitted.

- `./verify.sh`: **619 passed**
- `./verify.sh --fault`: **624 passed**
- Zero skips. Seed-11 floors PASS and reproduce. Seed 7 reproduces byte for byte.
- Corpus, recordings and dependencies are unchanged. No reviews, model calls or pushes ran.

Reports were regenerated with:

```sh
uv run docket eval --seed 7 --mode replay --budget 0
uv run docket eval --seed 11 --mode replay --budget 0
```

Files grouped as requested:

- **(a) Docs:** `README.md`; `docs/{product-brief,operations,build-log}.md`; ADR index and records `005–007`; failure index and entries `0002`, `0009`, `0075`, `0176`; [builder result](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/pr4-result.md).
- **(b) Code fixes:** `docket/{workflows,activities,evaluation}.py`; `web/login.html`.
- **(c) New tests:** `tests/workflow/test_optional_suggestion.py`; `tests/test_pr4_regressions.py`; `web/tests/login.spec.ts`.
- **(d) Existing-test changes:** `tests/workflow/test_errors.py` now requires choose errors to continue through scoring and filing, retaining exact error reasons and single finalization. `test_workflow.py` requires a choose replay miss to enter review and expire; genuine failure paths remain covered. `tests/browser/test_review_browser.py` checks return code before artifact copy and requires all 11 browser cases. The [result’s assertion table](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/pr4-result.md) lists each change and retained guard.
- **(e) Regenerated reports:** `evals/reports/2026-11-11-seed-7.{json,md}` and `2026-11-11-seed-11.md`. Seed-11 JSON is unchanged.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · branch `docs` · base `43d4825` · [local review page](http://127.0.0.1:8010/).