You are grading PR1 (phases P1+P2) of Docket v0.1 in this repo (branch v01, diff 52f9b8b..HEAD). Read builds/v01/plan.md (Definitions, P1, P2) and the changed source files (docket/, tests/, scripts/, verify.sh, docker-compose.yml, alembic/, config/, .github/). Grade against this bar. For EACH criterion output PASS or FAIL with specific evidence (file/line or behavior). Be strict: if not clearly met, FAIL. Also flag any real bug outside the criteria. Return exactly the JSON specified below, no Markdown or prose.

Criteria:
- PR1-1: ./verify.sh runs ruff, pyright (strict on docket/core/), pytest with zero skips enforced, per-module branch coverage gate, and detect-secrets; the GitHub Actions workflow runs the same script.
- PR1-2: Per-module branch coverage gate enforces >=95% on candidates, scoring, ledger and >=90% on storage, and storage tests exercise a real S3-compatible server (SeaweedFS) for the happy path, not mocks.
- PR1-3: Both corpus seeds (7, 11) have >=150 docs covering every case type in plan P1.6 (incl. injection with a real non-gold record id, former_tenant, building_level, empty_text_layer, corrupt, encrypted, zero_byte, non_pdf, very_long, exact_duplicate), with labels; a test proves regeneration is deterministic on extracted text and labels.
- PR1-4: Scoring implements the 2-independent-signal rule: an explicit id alone (even repeated) is never strong; conflicts force review; injection-id + gold-address yields conflict -> review; tests cover each.
- PR1-5: Storage keys are built only from config/record/enum components validated against [A-Za-z0-9_-]+ (path traversal rejected); put_if_absent is a no-op for same key+hash and raises a typed error for same key with different hash.
- PR1-6: Ledger: event appends are idempotent on idem_key; status transitions use a whitelist and nothing leaves a terminal status.
- PR1-7: Model gateway: replay miss raises a non-retryable error; record mode refuses non-corpus sources; per-call worst-case budget pre-check and a persisted daily cap exist with tests; no live calls in tests.
- PR1-8: No secrets committed (only .env.example); compose defines healthchecks for postgres, temporal and the S3 server; Temporal state persists on a volume.

Output contract:
{"family":"fable","model":"claude-fable-5-2","criteria":[{"id":"<plan criterion id>","criterion":"<exact criterion text>","verdict":"PASS","evidence":"<file:line or test evidence>"}],"findings":[{"likelihood":5,"impact":5,"protected":false,"trigger":"<concrete normal-use input, command or call path>","issue":"<real bug outside the criteria>","file":"<path>","line":"<line>","evidence":"<evidence>"}]}
family, model and a non-empty criteria array are required. findings optional. verdict is exactly PASS or FAIL. likelihood and impact are integers 1-10.
