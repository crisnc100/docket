# Docket v0.1 — plan (rev 2, after Fable 5.2 Bounce)

**Outcome:** A Python service where a submitted document (signed webhook or CLI) runs as a durable Temporal workflow. The workflow parses it, shortlists real candidate records with rules plus fuzzy matching, lets an LLM choose only among those candidates, and scores the choice deterministically. It then either files the document to content-addressed storage or routes it to review.

Every document ends in a terminal status with a reason. Fault-injection tests show **exactly-once effects**: execution is at-least-once, and every effect is idempotent. A held-out eval reports the precision vs. auto-file-rate curve against a rules-only baseline.

**PRs:** three PRs, stacked, each merged with a merge commit.
- **PR1:** P1 + P2, foundations and the domain core. Main works on its own: the corpus, a CLI that shortlists and scores a PDF, and the ledger. No workflow yet.
- **PR2:** P3, the workflow, plus the fault-injection suite and the eval. This is the risk core, reviewed on its own.
- **PR3:** P4 + P5, the API, the UI and docs.

## Stack (fixed)
- **Core:** Python 3.12 (uv), FastAPI, uvicorn, python-multipart, Pydantic v2, pydantic-settings, PyYAML, SQLAlchemy 2, Alembic, psycopg.
- **Workflow:** temporalio.
- **Documents and matching:** pypdf, rapidfuzz, reportlab, Faker.
- **LLM:** langchain-openai (OpenRouter).
- **Storage:** boto3 (S3-compatible only; SeaweedFS (S3) locally).
- **CLI and HTTP:** Typer, httpx.
- **Tests and checks:** pytest, pytest-asyncio, pytest-cov, ruff, pyright (strict on `docket/core/`), detect-secrets.
- **Web:** Vite + TypeScript, a static build served by FastAPI. Playwright for one smoke test.
- **Compose:**
  - Postgres 16
  - Temporal dev server (`temporalio/temporal server start-dev --ip 0.0.0.0 --db-filename /data/server/temporal.db`, on a volume)
  - SeaweedFS (S3)
  - api and worker
- New dependencies outside this list need a callback.

## Definitions (fixed before any code)
- **Identity and dedup:**
  - **Ingress order:** the API writes the raw bytes to `raw/<sha256>`, then inserts `documents` (unique on `(mode, run_id, content_hash)`), then starts the workflow.
  - **Workflow:** id `doc-<document_id>`, with `WorkflowIdReusePolicy.REJECT_DUPLICATE` and conflict policy `USE_EXISTING`.
  - **Resubmits:** an existing row returns that document. That holds after any terminal status and for a different `message_id`, which is recorded as another source in `document_sources`.
  - **Filing:** `filed_documents` is unique on `(mode, run_id, content_hash)`, so one filing per content per run.
  - Near-duplicates (same content, different bytes) are out of scope, as a documented limit.
- **Modes:** `act` uses run_id `live`. `observe` uses a fresh run_id per eval run, never writes storage beyond `raw/`, and the review step ends immediately as `would_review`.
- **Storage key:** `{building}/{record_id}/{category}/{sha256[:16]}.pdf`. Every component comes from the records config or an enum, never from sender text. Components are validated against `[A-Za-z0-9_-]+`. The original filename is object metadata only.
- **Terminal statuses:**
  - `filed`, `dismissed`, `needs_review_expired`, `would_file`, `would_review` (observe)
  - `rejected` (no_attachment, unsupported_type, zero_byte, encrypted, too_large, corrupt)
  - `failed` (non-retryable error, retries exhausted, orphaned)

  Activities have bounded retries (max 5, exponential backoff). Parse, encrypt, budget and replay-miss errors are non-retryable. The workflow body guarantees that `finalize` runs, and `docket reconcile` marks rows that are non-terminal for longer than N minutes and have no running workflow as `failed: orphaned`.
- **Corroboration score:** a deterministic weighted score in [0,1] from independent signals: explicit id, address match, tenant-name match, building match, filename id. Strong evidence needs **at least 2 independent signals agreeing**, so an explicit id alone caps at `medium`. The auto-file threshold is in config, and the curve sweeps it.
- **Eval split:** tune thresholds on corpus seed 7 and report on held-out seed 11. Metrics:
  - auto-file precision and recall
  - auto-file rate
  - review rate
  - wrong-file count
  - category accuracy
  - per-case-type outcomes
  - Wilson 95% intervals
- **Rules-only baseline:** take the top-scored candidate with no LLM, using the same threshold. This is also the "legacy process" in the shadow diff.

## Phases

### P1 — Foundations and corpus (PR1)
1. **Scaffold:** uv project, Typer CLI, ruff and pyright, pytest with zero skips allowed, `verify.sh`, a GitHub Actions workflow running it (Postgres service, Temporal time-skipping test env), `.env.example`, Apache-2.0.
2. **Compose and Alembic.** Tables:
   - `documents`: id, mode, run_id, content_hash, status, status_reason, category, record_id, score, timestamps
   - `document_sources`: document_id, source, message_id, sender, received_at
   - `events`: document_id, step, idem_key unique, detail jsonb, at
   - `filed_documents`
   - `budget_ledger`: day, spent_usd

   The review state is `documents.status`, so there is no separate review table.
3. **Model gateway** (`docket/llm/`), specified here in full:
   - An OpenRouter `ChatOpenAI` with json_schema structured output, pinned to supporting providers, with a Pydantic parse fallback. Unparseable output is a typed `ParseError`.
   - **Replay:** a JSONL store keyed by hash(activity, prompt_version, model, normalized input digest), with `record|replay|live` modes. A miss raises a non-retryable error. **Record mode refuses any source except the synthetic corpus.**
   - **Cost and budget:** cost comes from OpenRouter usage. A worst-case pre-check runs per call. The budget is per run, plus a **daily global cap persisted in `budget_ledger`** for the long-lived worker. 429 backoff.
4. **Model spike** (live, $0.50): confirm that the default cheap model honours a per-call **enum** json_schema on OpenRouter. Record the result in an ADR.
5. **Config:** `records.csv` holds about 200 units with record_id `UNIT-####`, address, building, current tenants, **former tenants** and aliases. `categories.yaml` (schema_version 1) covers lease, inspection, invoice, notice and misc, plus required signals.
6. **Corpus generator** `docket corpus generate --seed N`:
   - **Settings:** reportlab with `invariant=1`, ≥150 docs per seed.
   - **Labels:** `{record_id|none, category, expected: filed|review|rejected, duplicate_of?, case_type}`.
   - **Case types:**
     - clean
     - id_in_filename_only
     - address_only
     - tenant_only
     - former_tenant
     - two_record_conflict
     - decoy_near_miss_address
     - building_level
     - no_record
     - **injection with a real record id that is not the gold record**
     - exact_duplicate
     - empty_text_layer
     - corrupt
     - encrypted
     - zero_byte
     - non_pdf
     - very_long
   - Seeds 7 and 11 are committed. The determinism test compares extracted text and labels after regeneration, not raw bytes.

**Exit:** `./verify.sh` green. The compose services pass healthchecks (pg_isready, Temporal `operator cluster health`, SeaweedFS `/healthz` and a boto3 S3 call). The gateway replay, record-refusal, budget pre-check, daily-cap and corpus-determinism tests pass, and the spike is recorded.

### P2 — Domain core (PR1, plain Python, `docket/core/`)
1. **Extraction:** pypdf with typed results and rejection reasons. Truncation for the LLM keeps the first 6k characters plus the last 2k.
2. **Candidates:** the ID pattern (body, subject, filename), then normalized address, then rapidfuzz on tenant and former-tenant names, giving the top k=5 with per-signal scores. A **conflict flag** goes up when two distinct record ids appear anywhere.
3. **Scoring:** the corroboration score as defined above, with tiers derived from config thresholds.
4. **Storage:** `S3Storage.put_if_absent(key, bytes, sha256)`. The same key and hash is a no-op. The same key with a different hash raises a typed error, which leads to `failed: storage_conflict`. Key building validates its components.
5. **Ledger:** event append keyed by `idem_key = f"{document_id}:{step}"`, so a retry doesn't duplicate the event. Status transitions go through a whitelist, and nothing leaves a terminal status.

**Exit:**
- tests for every candidate rule and conflict case
- table-driven score tests, including "an explicit id alone is never strong" and "injection id plus gold address gives conflict, which goes to review"
- storage idempotency and key-sanitization tests (path traversal attempts rejected)
- transition whitelist tests
- branch coverage ≥95% per module on candidates, scoring and ledger, and ≥90% on storage against SeaweedFS (S3), with no mocks for the happy path

### P3 — Workflow, fault injection, eval (PR2)
1. **`FileDocumentWorkflow`** takes a storage key as input, never document bytes. Activities: extract, classify, shortlist, choose, score, file, finalize. Each has a start_to_close timeout (30s) and a heartbeat timeout (10s) where it's long-running.
2. **`classify`:** a structured category enum. Unknown values become `misc`.
3. **`choose`:** a per-call enum of the shortlist ids plus `none`. With an empty shortlist the LLM is not called.
4. **Routing:** at or above the threshold, file. Below it, `needs_review` waits on a signal: `approve(record_id)` with the record id validated against the shortlist or the records table, or `dismiss(reason)`. A timeout from config ends as `needs_review_expired`. A duplicate or late signal is ignored, logged and reported to the API as 409.
5. **Determinism:** a workflow replay test using Temporal's `Replayer` against recorded histories. ADR: patching strategy for in-flight review workflows across deploys.
6. **Failpoints:** `DOCKET_FAILPOINT=<activity>.<point>` calls `os._exit(137)` in the worker. It is only active when the env var is set.
7. **Fault suite** (`tests/fault/`, against compose):
   - kill after the storage write and before the ledger write, then restart: exactly 1 object, 1 `filed_documents` row, 1 `filed` event, status `filed`
   - kill during `choose`: same assertions
   - the API crashes after the raw write and before the `documents` insert: resubmit gives one document. After the insert and before workflow start, reconcile starts the workflow.
   - 3 concurrent submits plus 1 resubmit after `filed` via HTTP, with a different `message_id`: one document and one filing
   - a SeaweedFS (S3) transient error, retried: one object
   - each test **asserts that the failpoint fired**, from the worker's exit code and a log line
8. **Eval** `docket eval --seed 11 --mode replay|record|live --budget N`:
   - Docket vs. the rules-only baseline
   - a threshold curve with at least 8 points
   - a per-case-type table, plus category accuracy
   - committed `evals/reports/<date>.md` and `.json`, plus replay recordings
   - CI reproduces it, and `docs/rerecord.md` documents re-recording
9. **Shadow diff:** `docket shadow-diff` compares observe-mode Docket with the rules-only "legacy" baseline and classifies each disagreement against gold.

**Exit:** workflow tests (time-skipping env) cover happy, duplicate, injection, empty shortlist, approve, dismiss, timeout, double approve, approve after expiry, unknown id, observe mode, poison documents and the failed path. The fault suite passes. The replay eval reproduces. The live eval (≤$2) is committed.

**Floors on held-out seed 11, at the default threshold:** wrong-file count = 0, auto-file rate ≥90% on clean cases (single-signal cases are expected to go to review under the 2-signal rule), injection cases auto-filed to the injected id = 0, and Docket recall ≥ the rules-only baseline.

### P4 — API, CLI, review UI (PR3)
1. **FastAPI:**
   - `POST /inbound` takes JSON with base64 attachments, message_id and from. It requires an **HMAC-SHA256 signature header** with a shared secret, has a per-IP rate limit and a 10 MB limit, and creates one document per attachment.
   - `POST /documents` is the upload.
   - `GET /documents/{id}` returns the trace.
   - `GET /documents/{id}/file` is a proxied preview, so no presigned URLs are needed.
   - `GET /review`, `POST /review/{id}/approve|dismiss`, `GET /healthz`.
2. **CLI:** `docket submit`, `docket worker`, `docket status`, `docket reconcile`.
3. **Vite + TypeScript static page** served by FastAPI: the review queue (candidates with scores, reason, preview, pick and approve, dismiss) and a trace page.

**Exit:** API tests, including signature rejection and 409 on a duplicate approve. A Playwright smoke test approves one review item end to end on compose in replay mode.

### P5 — Docs (PR3)
- README: problem, mermaid diagram, quickstart, the held-out eval curve against the baseline with intervals, the fault-suite summary, limits (near-duplicates, OCR)
- `docs/product-brief.md`
- `docs/adr/`, at least 3 measured ADRs: candidates-only LLM choice, the 2-signal strong rule, Temporal over a hand-rolled claim lock (measured by the fault suite). Unbuilt alternatives are labelled "not measured".
- `docs/build-log.md` (journal, kept on purpose)
- `docs/failures/` with 3 or more entries from the eval

## Acceptance criteria
- **D1** `./verify.sh` passes: ruff, pyright (strict on core), unit and workflow tests with zero skips, per-module coverage, the replay eval reproduction, detect-secrets, `docker compose build`. The GitHub Actions workflow runs the same script. *CI-green is checked after the first push, which needs Cristian's `gh auth login`.*
- **D2** Per-module branch coverage: ≥95% on candidates, scoring and ledger, and ≥90% on storage against real SeaweedFS (S3).
- **D3** Both corpus seeds have ≥150 docs covering every case type in P1.6, and regeneration is deterministic on extracted text and labels.
- **D4** Injection safety:
  - The choose schema is restricted to the shortlist.
  - No injection document is ever auto-filed to its injected id, in tests or across the held-out eval.
  - An explicit id alone never scores strong.
  - An empty shortlist makes zero LLM calls (gateway call count).
- **D5** Every document across the whole held-out eval, including poison cases, ends in a terminal status with a non-empty reason. `reconcile` converts orphans (test).
- **D6** Dedup holds through HTTP: concurrent ×3, resubmit after a terminal status, and a different `message_id` all give one document and ≤1 filing.
- **D7** The fault suite in P3.7 passes, with failpoint-fired assertions and single-event checks.
- **D8** Review: approve, dismiss, timeout, double approve (409), approve after expiry (409) and an unknown record id (rejected) are all tested.
- **D9** Observe mode writes no objects outside `raw/` and no `act` filings, and its review step ends `would_review` without waiting.
- **D10** The held-out floors in P3 are met. The report includes the ≥8-point curve, the baseline comparison, the per-case-type table, category accuracy and intervals. The replay eval reproduces.
- **D11** The shadow-diff report classifies every disagreement against gold.
- **D12** Webhook: an unsigned or invalid signature gives 401, oversize gives 413, and rate limiting is enforced (tests).
- **D13** The Playwright smoke test approves a review item end to end.
- **D14** Compose healthchecks pass for all services, and the api and worker Dockerfiles build.
- **D15** Docs: README, brief, ≥3 ADRs each with a prediction committed before its measured result, failures. No secrets are committed.

## Decision 2026-11-09 (Cristian): no OpenRouter key for now; simulate model behaviour
- **The gateway gains an export/import path.**
  - `--mode export` writes each pending model request to `evals/pending/<key>.json`: system prompt, messages, response schema, target model id.
  - The orchestrator has a Claude subagent answer each request as the product model would, using the product's system prompt and returning exactly the response schema.
  - `--mode import` loads those answers into the replay store with provenance `{"simulated_by": "<claude model id>", "standing_in_for": "<target model id>"}`.
- **What changes in the plan:**
  - **"Live" model spikes:** replaced by a simulated schema-compliance check. The real-model spike is deferred until a key exists, and ADR-004 records that.
  - **"Live" eval runs:** become simulated runs, and every simulated report is labelled at the top: "Model responses simulated by <claude model>, standing in for <target>; not an OpenRouter measurement."
  - **Budget caps and live mode:** stay in the code for when a key is added.
- **Floors** apply to the simulated run, and are re-measured on a real model when a key is added.
