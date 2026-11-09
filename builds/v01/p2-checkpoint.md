# P2 checkpoint — PASS

P2.1–P2.5 are complete locally. `./verify.sh` exits 0 with 192 passed tests and
zero skips. All four required modules have 100% measured branch coverage.
Changes remain in the worktree for the operator to commit. No commit was attempted.

## What was built

- Typed PDF extraction/rejections and exact first-6000/last-2000 truncation.
- Real-record top-five candidates from body/subject/filename IDs, normalized
  addresses/aliases, current/former tenants and explicit building codes; stable
  ordering, per-signal scores and conflict detection before shortlist truncation.
- Deterministic corroboration scoring with the approved configurable policy,
  independent-signal gate, conflict override and table-driven tests.
- Generic S3 conditional-create storage, content-hash checks, retry no-ops,
  typed conflicts, safe deterministic filing keys, and original-filename metadata.
- Transactional Postgres event idempotency and locked status transitions through
  an explicit whitelist, with terminal states immutable and mode/reason guards.
- Per-module branch coverage enforcement in `scripts/check_coverage.py`, wired
  into verify.sh, with tests rejecting missing/invalid/line-only coverage reports.
- CI starts real SeaweedFS S3 for the storage tests. Bucket auto-creation is
  disabled locally to match standard S3 behavior.

## P2 exit checks

| Exit check | Result | Evidence from `./verify.sh` |
| --- | --- | --- |
| P2.1 typed extraction and truncation | PASS | 14 extraction tests; 14/14 branches |
| Every candidate rule and conflict case | PASS | 28 candidate tests; 20/20 branches |
| Table-driven score tests | PASS | 43 scoring tests; requested table below |
| Explicit ID alone/repeated is never strong | PASS | Real candidate evidence stays .30/medium; dominant single-signal weights are also capped below strong |
| Injection ID plus gold address conflicts and reviews | PASS | Both gold and injected choices return review, score 0 and reason conflicting_records |
| Storage idempotency and key sanitization | PASS | 24 storage tests against real SeaweedFS, including same-key retry, different-hash conflict, concurrent create and traversal rejection |
| Transition whitelist and terminal immutability | PASS | 19 ledger tests against real Postgres, including all 100 status pairs, concurrent event insertion and atomic rollback |
| Candidates branch coverage >=95% | PASS | 20/20 = 100.00% |
| Scoring branch coverage >=95% | PASS | 30/30 = 100.00% |
| Ledger branch coverage >=95% | PASS | 16/16 = 100.00% |
| Storage branch coverage >=90% | PASS | 20/20 = 100.00% |
| Strict pyright on core | PASS | 0 errors, 0 warnings, 0 informations |
| Zero skips | PASS | 192 passed; no skipped tests |
| Ruff and formatting | PASS | Both commands in verify.sh exit 0 |
| Alembic and P1 regressions | PASS | Existing migration and all 54 P1 tests pass unchanged |
| Secrets | PASS | detect-secrets: {} |
| `./verify.sh` overall | PASS | Exit 0 |

## Approved scoring table

All numbers are **untuned defaults** in `config/scoring.json`; tuning is reserved
for P3 on seed 7. Seed 11 was not used for tuning.

Weights: ID .30, address .30, tenant .25, building .10, filename .05.
Medium threshold .25; strong threshold .55; default auto-file threshold .55.

| Evidence | Score | Tier | Decision |
| --- | --- | --- | --- |
| ID + address | .60 | strong | file |
| ID + tenant | .55 | strong | file |
| Address + tenant | .55 | strong | file |
| ID alone | .30 | medium | review |
| ID repeated 3x | .30 | medium | review |
| Building + filename | .15 | none | review |
| Matching body ID + filename ID | .35 | medium | review |
| Filename alone | .05 | none | review |
| Injection ID + gold address | 0 (conflict override) | none | review |

Strong additionally requires at least two independent positive-weight signals.
Repeated body/subject IDs contribute once. A filename match may count separately
when it agrees with the chosen record; mismatching body/filename IDs create a
conflict and force review. A building inferred only from an address contributes
neither weight nor independence. The matcher supplies explicit-building provenance.
Single-signal scores are capped below strong even if weights are reconfigured.
Raw weighted scores are retained separately for diagnostics; conflict overrides
and caps determine the effective score used for threshold decisions.

## Command evidence

```text
$ docker compose up -d --wait
Container docket-v01-postgres-1 Healthy
Container docket-v01-seaweedfs-1 Healthy
Container docket-v01-temporal-1 Healthy
Container docket-v01-temporal-data-init-1 Exited
```

```text
$ ./verify.sh
All checks passed!
0 errors, 0 warnings, 0 informations
collected 192 items
tests/core/test_candidates.py ............................
tests/core/test_coverage_gate.py ..........
tests/core/test_extraction.py ..............
tests/core/test_ledger.py ...................
tests/core/test_scoring.py ...........................................
tests/core/test_storage.py ........................
tests/test_config.py ........
tests/test_corpus.py ......
tests/test_database.py ......
tests/test_gateway.py .............................
tests/test_scaffold.py .
tests/test_spike.py ...
tests/test_temporal_environment.py .
192 passed in 20.52s
PASS docket/core/candidates.py: 20/20 branches = 100.00% (required 95%)
PASS docket/core/scoring.py: 30/30 branches = 100.00% (required 95%)
PASS docket/core/ledger.py: 16/16 branches = 100.00% (required 95%)
PASS docket/core/storage.py: 20/20 branches = 100.00% (required 90%)
detect-secrets: {}
[exit 0]
```

The coverage report also measured extraction at 14/14 branches; total core branch
coverage was 100/100. The gate uses branch-only counters, not the combined
percentage printed by coverage.py. Raw machine-readable results remain at
`.cache/coverage.json` (ignored).

Focused scoring command:

```text
$ uv run pytest tests/core/test_scoring.py --cov=docket.core.scoring --cov-branch --cov-report=term-missing --cov-report=json:.cache/p2-scoring-coverage.json
43 passed in 0.19s
scoring: 30 branches, 0 partial, 100%
```

The real S3 conditional-write probe returned:

```text
first conditional PUT 200
second conditional PUT PreconditionFailed 412
stored body a
```

## Deviations and deliberate limits

- Scoring waited for the operator's policy choice; independent storage, ledger
  and coverage work continued during that wait. The approved .55 thresholds
  replace the earlier .70 proposal. No policy question remains open.
- The first negative S3 test exposed SeaweedFS auto-creating absent buckets.
  Disabled that Compose option, retained the assertion and reran against real S3.
  No mocks were used for the storage happy path, retries, concurrency or conflicts.
- Core uses a typed S3 client protocol exercised with boto3 rather than adding SDK
  stub dependencies or suppressing strict pyright. Application code uses only S3.
- StorageConflict exposes storage_conflict as its reason. Mapping that exception
  to a workflow's failed terminal status belongs to P3; no workflow was built.
- The ledger's identical-status retries are no-ops. HTTP rejection of late review
  actions belongs to P3/P4. Caller-owned transactions keep status/events atomic.
- Scores use Decimal and seven fractional digits to match the existing database
  score precision. A one-signal cap is one score quantum below the strong threshold.
- No existing tracked tests/checks were weakened, skipped or deleted. No extra
  dependencies, secrets, P3+ implementation, model calls, reviews, grades, commits,
  pushes or PRs were added/performed. CI is configured but has not run remotely.
- The P1 live model spike remains pending without a key; it is not a P2 exit check.

## Open questions

None for P2. Stop here. P3 owns seed-7 tuning, workflows, fault tests and evaluation.

## Location

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `v01`; base commit: `ac20871`; changes uncommitted for the operator.
No application URL in P2. S3: `http://127.0.0.1:9000`; Temporal UI:
`http://localhost:8233`; Postgres: `127.0.0.1:55432`.
