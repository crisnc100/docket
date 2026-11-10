# P1 checkpoint — checks pass; commit blocked; live spike pending

## Built

P1.1–P1.6: uv/Python 3.12 package, Typer CLI, lint/type/zero-skip/secret checks,
GitHub Actions, Apache-2.0, Compose infrastructure, Alembic's five-table schema,
budgeted OpenRouter gateway with JSONL record/replay, enum spike command and ADR,
200-unit records config, five categories, and deterministic corpus seeds 7/11.
Each seed contains 170 documents (ten of each of the 17 planned case types).

## Exit checks

| Exit check | Result | Evidence |
| --- | --- | --- |
| `./verify.sh` | PASS | ruff clean; 33 files formatted; pyright 0 errors; 54 tests passed; secrets `{}` |
| Postgres health | PASS | `pg_isready`: accepting connections |
| Temporal Compose health | PASS | `operator cluster health`: SERVING; uid 1000 owns the persisted DB directory |
| SeaweedFS health | PASS | `/healthz` exits 0; Compose reports healthy; authenticated boto3 S3 call returns HTTP 200 |
| Alembic schema | PASS | Six database tests: upgrade/downgrade, metadata comparison, dedup constraints, JSONB, sources, event uniqueness, foreign keys |
| Gateway replay | PASS | Recorded output replays without transport/key; changed contract misses non-retryably |
| Record refusal | PASS | Five non-corpus sources rejected before any call or write |
| Budget pre-check | PASS | Per-run and global limits block before transport; run budget shared across gateways |
| Persisted daily cap | PASS | Eight concurrent reservations through separate budget objects admit exactly three $0.03 reservations under a $0.10 cap |
| Schema, parsing, cost and 429s | PASS | HTTP transport test verifies strict enum/provider/price fields and usage cost; invalid parses are typed and charged; backoff is bounded |
| Config | PASS | Eight tests validate 200 unique safe records, tenant/former-tenant/aliases, schema version and all five categories |
| Corpus | PASS | Six tests validate both seeds, all case types, labels, poison fixtures, real adversarial ids and regeneration by extracted text/labels |
| Live model spike | PENDING — permitted exception | Key is absent; command and ADR exist. No live call and no fabricated result |

## Command output

```text
$ ./verify.sh
Resolved 88 packages in 2ms
All checks passed!
33 files already formatted
0 errors, 0 warnings, 0 informations
collected 54 items
tests/test_config.py ........
tests/test_corpus.py ......
tests/test_database.py ......
tests/test_gateway.py .............................
tests/test_scaffold.py .
tests/test_spike.py ...
tests/test_temporal_environment.py .
54 passed in 4.71s
detect-secrets: {}
```

```text
$ uv run docket corpus generate --seed 7
Generated 170 documents: corpus/seed-7
$ uv run docket corpus generate --seed 11
Generated 170 documents: corpus/seed-11
```

```text
$ docker compose up -d --wait
Container docket-v01-postgres-1 Healthy
Container docket-v01-seaweedfs-1 Healthy
Container docket-v01-temporal-1 Healthy
Container docket-v01-temporal-data-init-1 Exited

$ docker compose exec -T postgres pg_isready -U docket -d docket
/var/run/postgresql:5432 - accepting connections

$ docker compose exec -T temporal temporal operator cluster health --address 127.0.0.1:7233
SERVING

$ docker compose exec -T seaweedfs wget -q -O - http://localhost:9333/healthz
[no body; exit 0]

$ docker compose ps --format '{{.Service}} {{.State}} {{.Health}}'
postgres running healthy
seaweedfs running healthy
temporal running healthy
```

The healthchecks also passed after `docker compose restart temporal seaweedfs`.
An authenticated boto3 client configured only with the generic S3 endpoint,
local credentials, region and path-style addressing returned:

```text
S3 list_buckets HTTP 200
```

Key availability was checked without displaying its value:

```text
OPENROUTER_API_KEY=NOT_SET
```

## Deviations and deliberate limits

- SeaweedFS replaces MinIO by explicit operator authorization; exact tag 4.53.
  The plan's P1 health endpoint and Temporal DB path reflect the authorized fix.
- Local Postgres uses 55432 to avoid the existing host service; CI overrides its
  URL to the isolated service at 5432. No host database was modified.
- Daily budget reservations remain charged after ambiguous failures or crashes.
  Per-run state is process-local and must be shared across gateways in a run.
- The live spike is PENDING. Its request format is tested locally, but provider
  compliance and live usage cost are unmeasured.
- No P2+ behavior, review, grade, other-model call, push or PR was performed.
  No existing tracked tests were modified, weakened or skipped.
- CI is configured but has not run remotely; there was no push.
- Raw harness logs are preserved locally and ignored. Build plans, briefs,
  checkpoints, ADR and deliberate choices are ready for the phase commit.

## Commit blocker

The phase-end commit could not be created. The sandbox allows writes in the
worktree but denies writes to its shared Git metadata outside that directory.
No files were staged and no commit was made; the checked implementation and
build artifacts remain in the worktree.

```text
$ git add [phase files and build artifacts]
fatal: Unable to create '/Users/cortega/Documents/Projects/docket/docket/.git/worktrees/docket-v01/index.lock': Operation not permitted
```

This needs a session permitted to write that Git metadata. No permission override
or alternate Git directory was used.

## Open questions

No unresolved implementation choice. The phase-end commit is blocked by the
sandbox. Recommended next action: the orchestrator creates the phase commit in a
session with Git metadata write access, verifies P1, then starts P2. Run the live spike with an OpenRouter
key before depending on that model/provider's enum compliance; the $0.50 run cap
and persisted daily cap apply. The prediction/protocol is in
`docs/adr/001-model-enum-spike.md`.

## Location

- Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`
- Branch: `v01`; starting base: `c97253a`
- No application URL in P1; infrastructure: Postgres `127.0.0.1:55432`, Temporal
  `localhost:7233` (UI `http://localhost:8233`), S3 `http://127.0.0.1:9000`.
