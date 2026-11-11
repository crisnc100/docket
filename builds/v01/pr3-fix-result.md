Fixed s1 and all five LOWs. Reviewer access now requires a Bearer token and returns 503 when unconfigured. The page uses sessionStorage and authenticated blob previews; the CLI reads the environment token.

- `./verify.sh`: **604 passed**
- `./verify.sh --fault`: **609 passed**
- Zero skips; seed-11 reproduces PASS; no secret findings.
- Choose-failure behavior remains filed for PR4. Corpus/eval files are unchanged.
- Left uncommitted on `review-ui`.

**(a) New tests**

- `tests/workflow/test_review_access.py`
- `tests/test_review_cli_auth.py`
- `tests/test_webhook_timestamp_validation.py`

**(b) Existing-test changes**

| Files | Change and retained guard |
|---|---|
| `tests/workflow/test_review_api.py` | Future timestamp **+301 → +400 seconds**; still asserts **401** for timestamps beyond five minutes. Added credentials to the protected-route client. |
| `tests/test_review_cli.py` | Added Bearer-header assertions; retained upload-byte, URL, status and error checks. |
| `web/tests/review.spec.ts` | Added token entry, rejection, sessionStorage and reload checks. Preview assertion now requires a blob URL; PDF-byte, viewer, filing and XSS assertions remain. |
| `tests/workflow/conftest.py`, `tests/workflow/test_integration.py`, `tests/fault/test_faults.py`, `tests/browser/test_review_browser.py` | Added generated test tokens and credential plumbing. Existing assertions unchanged. |

**(c) Fix files**

- `docket/{api,review_access,cli,settings,webhook}.py`
- `web/login.html`, `web/src/main.ts`
- `compose.yaml`, `.env.example`, `.dockerignore`, `README.md`

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · Base: `a9287a8` · [Local page](http://127.0.0.1:8010/)