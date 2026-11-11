P4 is implemented and left uncommitted on `review-ui`.

- `./verify.sh`: **572 passed, zero skips**
- `./verify.sh --fault`: **577 passed, zero skips**
- Both runs reproduced seed 11 with all floors PASS and no secret findings.
- Review suggestions: model **44/102 (43.1%)** correct; rules **62/102 (60.8%)**. No new simulation or runtime dependencies.

| Criterion | Proof |
|---|---|
| D8 | HTTP approve, duplicate, unknown-ID and expiry tests in `tests/workflow/test_integration.py`; timeout in `test_workflow.py`; dismiss in `test_review_api.py` |
| D12 | `test_webhook_authentication_rate_limit_and_concurrent_idempotence`; `test_limit_rejects_without_reading_remainder` |
| D13 | Playwright “queue → document → approve suggested → filed” test, including the native PDF viewer |
| D16 | `test_suggestion_never_changes_rules_tier_or_filing`; threshold-curve invariant test; `scripts/reproduce_eval.py` |
| D17 | Playwright script-filename-and-sender test verifies literal text and no injected script elements |

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`  
Branch: `review-ui` · Base: `a9287a8` · URL: [local review page](http://127.0.0.1:8010/)

Files, grouped as requested:

- **(a) Phase-1 code:** `docket/{api,webhook,ingress,activities,workflows,settings,cli,evaluation}.py`, `scripts/reproduce_eval.py`, `.env.example`, `compose.yaml`, `README.md`. API and Compose also contain phase-2 wiring.
- **(b) Phase-2 code:** `web/index.html`, `web/src/{main.ts,style.css}`, `web/{package.json,package-lock.json,tsconfig.json}`, `Dockerfile`, `.dockerignore`, `.gitignore`, `.github/workflows/verify.yml`, `verify.sh`.
- **(c) New tests:** `tests/workflow/test_review_api.py`, `tests/{test_webhook_limits,test_review_cli,test_review_comparison}.py`, `tests/browser/{conftest,test_review_browser}.py`, `web/playwright.config.ts`, `web/tests/review.spec.ts`.
- **(d) Existing-test changes:** none.
- **(e) Generated data:** `evals/reports/2026-11-11-seed-11.{json,md}`, generated through `uv run docket eval --seed 11 --mode replay --budget 0`. Corpus, recordings and historical reports are unchanged.