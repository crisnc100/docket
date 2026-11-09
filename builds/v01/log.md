# Docket v0.1 build log

## P1 checkpoint — 2026-11-09

P1.1 is complete locally. P1.2 is interrupted by a stack/toolchain decision. P1.3–P1.6 have not started. No P2+ implementation, live model call, review, push or PR was performed.

### Deliberate choices

- Python is pinned to 3.12. The global managed 3.12 interpreter failed with `Bad CPU type in executable (os error 86)`. A native interpreter and uv cache live under ignored `.cache/` inside this worktree; verify.sh selects those paths.
- uv_build is the uv project's packaging backend, not an application dependency. All direct runtime and check dependencies are from the fixed stack; uv.lock pins their resolutions.
- The Typer CLI currently exposes only `version`. No later-phase command is scaffolded.
- Pytest rejects skip/skipif markers during collection and fails the session on runtime skips. CI runs the same verify.sh, with a Postgres 16 service. The test suite exercises Temporal's real time-skipping test environment without a live model.
- Secret scanning includes untracked files. Generated caches and raw harness logs are excluded; known public local-development credentials have explicit allowlist annotations. No live credentials are included.
- Compose contains only the P1 infrastructure services. API and worker belong to later phases.
- Draft SQLAlchemy models and Alembic environment are present but no migration has been generated or applied; these are not claimed complete.

### Decision blocking P1.2

The fixed stack requires MinIO. The initial official Docker Hub image pull failed:

```text
$ docker compose up -d --wait
Image minio/minio:RELEASE.2025-09-07T16-13-09Z Error pull access denied for minio/minio, repository does not exist or may require 'docker login'
```

The alternative official registry also failed:

```text
$ docker pull quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z
unexpected status from HEAD request to https://quay.io/v2/minio/minio/manifests/RELEASE.2025-09-07T16-13-09Z: 401 UNAUTHORIZED
```

The official repository documents source-only distribution and a Go build requirement:
https://github.com/minio/minio#source-only-distribution

**Recommended answer:** authorize a pinned official MinIO source build in a Docker build stage, including the Go build toolchain. Keep MinIO as the runtime service. Do not substitute an S3 implementation or use an unvetted third-party image. This is a callback under the user's fixed-stack constraint; no source build or additional toolchain has been added.

### Exit checks and evidence

| P1 exit check | Result | Evidence |
| --- | --- | --- |
| `./verify.sh` on current implementation | PASS | Output below; this proves the current scaffold, not completion of unimplemented P1 tests. |
| Postgres `pg_isready` | PASS | `/var/run/postgresql:5432 - accepting connections` |
| Temporal Compose cluster health | FAIL | Server exited before healthcheck; log below. |
| MinIO `/minio/health/live` | FAIL | Image cannot be pulled; service never started. |
| Alembic schema and constraints | FAIL — not completed | No version migration yet; blocked during P1.2. |
| Gateway replay tests | FAIL — not implemented | P1.3 not reached. |
| Gateway record-refusal tests | FAIL — not implemented | P1.3 not reached. |
| Budget pre-check tests | FAIL — not implemented | P1.3 not reached. |
| Persisted daily-cap tests | FAIL — not implemented | P1.3 not reached. |
| Model spike and ADR | FAIL — not implemented | P1.4 not reached. Key availability has not been inspected; the no-key exception has not been claimed. |
| Records and categories config | FAIL — not implemented | P1.5 not reached. |
| Corpus seeds 7/11, case coverage and determinism | FAIL — not implemented | P1.6 not reached. |

```text
$ ./verify.sh
Resolved 88 packages in 4ms
Audited 85 packages in 7ms
All checks passed!
15 files already formatted
0 errors, 0 warnings, 0 informations
collected 2 items
tests/test_scaffold.py .
tests/test_temporal_environment.py .
2 passed in 0.32s
detect-secrets: {}
```

```text
$ docker compose up -d postgres temporal --wait
Image temporalio/temporal:1.5.1 Pulled
Image postgres:16 Pulled
Container docket-v01-temporal-1 Started
Container docket-v01-postgres-1 Started
container docket-v01-temporal-1 exited (1)

$ docker compose logs --no-color --tail 20 temporal
temporal-1 | time=2026-11-09T20:09:13.138 level=ERROR msg="failed starting server: failed setting up schema: unable to create SQLite admin DB: unable to open database file: out of memory (14)"
```

Temporal's volume/database startup failure remains undiagnosed. The time-skipping test environment passed independently; it does not prove Compose cluster health.

### Deviations and next action

- Stopped during P1.2 under the explicit callback condition. No tests were skipped or weakened.
- P1 is incomplete; no phase-end commit has been made. The existing plan/context/bounce files and harness-owned phase1 brief/logs were preserved.
- Resolve the MinIO build-toolchain choice, then finish P1.2 (including Temporal startup, migration and database tests), run verify.sh, and continue P1.3–P1.6 in order.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`; branch: `v01`; starting HEAD: `c97253a`; configured application URL: none yet. Infrastructure ports: Postgres 5432, Temporal 7233/8233, MinIO 9000/9001.

## P1 resumed — 2026-11-09

The operator rejected a MinIO source build and authorized SeaweedFS S3 instead.
The incomplete checkpoint above is historical; the final state and command
evidence are in `builds/v01/p1-checkpoint.md`.

Deliberate choices:

- SeaweedFS is pinned to official `chrislusf/seaweedfs:4.53`. Its single-process
  `mini` mode persists both objects and filer metadata under the named `/data`
  volume. Only the S3 port is published, on loopback at 9000. The healthcheck
  uses the master's `/healthz`; a separate boto3 `list_buckets` call confirms
  that the S3 endpoint accepts authenticated requests. No storage adapter was
  implemented in P1, and no application code depends on SeaweedFS-specific APIs.
- Temporal keeps its default uid 1000. A one-shot container using the same image
  creates and assigns `/data/server` on the named volume to that uid before the
  server starts. The SQLite DB is `/data/server/temporal.db`. Cluster health also
  passed after restarting Temporal and SeaweedFS.
- Local Postgres uses loopback port 55432 because a host Postgres already listens
  on 5432. CI uses its isolated Postgres service on 5432 via DATABASE_URL. Tests
  migrate an isolated schema per database fixture and drop only that schema.
- The initial Alembic migration owns the five planned tables and their indexes,
  foreign keys and dedup/event constraints. Review state remains in documents.
  Status transitions, the filing ledger's behavior and S3 storage belong to P2.
- OpenRouter is configured through ChatOpenAI with a strict per-call schema,
  only the configured provider, no provider fallback and require_parameters.
  Local Pydantic validation also checks the enum. The fallback parses raw JSON
  from the same paid response; it does not issue another request.
- Replay identities include activity, prompt version, configured model and the
  normalized digest of inputs, prompt and schema. Normalization canonicalizes
  JSON key order, Unicode NFC and newlines. JSONL appends use file locks, fsync
  and conflict detection. Record mode requires source `synthetic_corpus` before
  any budget or transport work, and recordings contain results, not raw inputs.
- One RunBudget instance must be shared across a run's gateway instances. It is
  process-local; the daily global cap is persisted and coordinated by Postgres.
  Daily rows temporarily include reservations. A conditional UPDATE prevents
  concurrent workers from exceeding the cap. Settling refunds the difference
  between the worst-case reservation and OpenRouter's actual usage cost against
  the original UTC day. A crash or ambiguous network failure retains its worst
  reservation for the day; this deliberately sacrifices availability for a safe
  spend limit. Explicit 429s refund the reservation and back off at 1/2/4/8 seconds,
  with at most five attempts; SDK retries are disabled.
- The default token price ceilings are $0.30/M input and $1.20/M output, enforced
  in OpenRouter's provider routing. Worst-case reservation bounds input tokens
  by serialized UTF-8 bytes plus 4096 framing tokens and uses the explicit output
  token limit. Missing/invalid usage cost retains the reservation and raises a
  typed error. No price estimate is presented as a measured cost.
- No OpenRouter key is set. The live enum spike is PENDING, with a command and
  prediction/protocol ADR committed. No result JSON or live model measurement
  was fabricated. Tests of the request wire format use local HTTP responses.
- The config contains 200 synthetic units in ten buildings. Tenant/former-tenant
  fields and address aliases are lists encoded with `|` in CSV. Pydantic validates
  the exact five categories, schema version, signals and safe record components.
  Category signal requirements are conservative initial config, not thresholds
  tuned against held-out seed 11. No candidate or scoring rules were implemented.
- Each seed has ten examples of all 17 case types (170 files). Gold for conflicts,
  building-wide, unknown and poison cases is none; injection gold remains a real
  unit and the adversarial id is a different real unit. Ambiguous fixtures expect
  review. Corrupt/encrypted/zero-byte/non-PDF fixtures use the corpus's coarse
  expected=rejected label; this is not a workflow terminal-status assertion.
- PDFs use reportlab invariant=1. Encryption fixtures use a public test password
  and RC4 solely to exercise password protection without an additional crypto
  dependency. Determinism compares labels and extracted text (decrypting the
  encrypted fixture with that password); encrypted bytes need not reproduce.
  Exact duplicates separately require byte identity with their clean source.
- Corpus generation uses local seeded Random and Faker instances. Seed 7 is for
  later tuning; seed 11 remains held out. Corpus labels express the desired gold
  outcome and do not claim that an unbuilt filing engine already achieves it.
- Tests use the ignored worktree-local cache for temporary regeneration artifacts.
  Intentional public local credentials and the synthetic PDF password are
  annotated for detect-secrets; live keys are never written. Harness-owned raw
  phase logs are ignored; build plans, briefs, checkpoints and this log are kept.

Validation progression: P1.1: 2 passed; P1.2: 8 passed; P1.3: 34 passed;
P1.4: 36 passed plus live spike PENDING; P1.5: 44 passed; P1.6: 54 passed (including the spike measurement/ADR writer tested locally).
All step verify.sh checks ended green after correcting the observed failures.
No skipped tests, added external runtime dependency, P2+ implementation, review,
grade, other-model call, push or PR was performed.


Phase-end commit attempt: `git add` failed creating the shared worktree
`index.lock` with `Operation not permitted`. The shared Git directory is outside
the sandbox's writable worktree. No staging, commit, push or workaround occurred.
The source and build artifacts remain intact for the orchestrator to commit from
a session with the required Git metadata write access.
