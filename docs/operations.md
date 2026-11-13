# Running Docket

A record is one property unit in `config/records.csv`.
All commands run from the repository root.
See the [quickstart](../README.md#quickstart) for the shortest path.

## How a document is matched

1. **Extract.** Docket reads the PDF text with pypdf. A file that cannot be read is
   rejected with a reason: `zero_byte`, `too_large`, `unsupported_type` (not a PDF),
   `encrypted` or `corrupt`.
2. **Shortlist.** Docket looks for evidence of each record and keeps up to five
   candidates. Each piece of evidence is a *signal*:
   - `explicit_id`: the record id (`UNIT-####`) in the text or subject.
   - `filename_id`: the record id in the file name.
   - `address`: the unit's address or one of its aliases. Spellings such as
     "Street"/"St" and "Apt"/"Unit"/"#" count as the same.
   - `tenant_name`: a current or former tenant's name. Close spellings count.
   - `building`: the building code. It counts only when it appears apart from the
     address text.

   Docket raises a *conflict* when the evidence names more than one record. Only
   three kinds of evidence count here: a record id (in the text, subject or file
   name), a matching address, and an exact tenant name.

   Document text is untrusted, so matching has one size budget per field: the body
   up to 64,000 characters and 2,000 lines, the subject up to 1,000 characters and
   the file name up to 255. A field over its budget is left out of matching and the
   document goes to review with the reason `input_too_large`. Docket does not shorten
   an over-budget field to fit, because the cut part could hold a contradicting address. Record
   ids in every field still count toward a conflict.
3. **Score.** Each signal has a weight in `config/scoring.json`. The weights are
   untuned defaults. Name and address strengths vary with match quality. The score
   is between 0 and 1 and falls in a tier: `none`, `medium` or `strong`.
   - `strong` needs at least two different signals. One signal alone, even a record
     id, never reaches `strong`.
   - A conflict sets the score to 0 and sends the document to review.
   - Only a `strong` match whose score reaches `auto_file_threshold` (0.5857142,
     chosen on seed 7) may be filed without a person.

Requiring two signals is the main defence against injected text. A document that
just names a record id cannot file itself.

## Workflow per document

`POST /documents` takes a PDF upload. Docket saves the raw bytes under their SHA-256
hash (a digest of the bytes) and starts one Temporal workflow for the document. Uploading the same bytes
again returns the same document id. `uv run docket worker` runs the worker.
`GET /documents/{id}` shows the status, the reason and every logged step.

The workflow runs these steps:

1. Extract the text, as above.
2. Shortlist candidate records, as above.
3. Ask the model for a category, then ask it to choose one shortlisted record or
   `none`. An empty shortlist skips both calls and the category is `misc`.
   A failed record-choice call leaves no suggestion. Its reason is saved in the
   score event, and scoring continues. An unreadable category answer, such as a
   refusal, sets the category to `misc`, records why in the classify event, and sends
   the document to review with the reason `category_unreadable`, even when the rules
   would file it. Any other failed category call, such as no answer, ends `failed`.
4. Score every candidate and take the rules' highest score for the tier and filing
   decision. The model's record choice is a suggestion for the reviewer.
5. File the document, or wait for a person. `POST /review/{id}/approve` with a
   `record_id` files it; `POST /review/{id}/dismiss` with a `reason` closes it. An
   unanswered review expires after `DOCKET_REVIEW_TIMEOUT_SECONDS` (one hour by
   default).

Every document ends with one final status and a reason: `filed`, `dismissed`,
`needs_review_expired`, `rejected` or `failed`. In `observe` mode `would_file` and
`would_review` replace the first three; `rejected` and `failed` still apply. `docket reconcile` starts workflows for
uploads that never got one and marks abandoned documents `failed`.
[ADR-002](adr/002-review-workflow-patching.md) explains how to change the
workflow while reviews are still waiting.

## Inbound webhook

`POST /inbound` takes email from a mail provider. Set `DOCKET_WEBHOOK_SECRET` to a
secret shared with the sender. With no secret set, every webhook request fails
with 401.

Send JSON with `message_id`, `from`, and an `attachments` list; each attachment has
`filename` and `content` (base64 bytes). Sign the exact body bytes:
`HMAC-SHA256(secret, timestamp + "." + raw_body)`. Send the Unix timestamp, in
whole seconds, in `X-Docket-Timestamp` and the hex digest prefixed with `sha256=` in
`X-Docket-Signature`. A timestamp more than five minutes from the server's clock,
in either direction, fails with 401. This stops an old request being sent again.

Each attachment becomes one document.
Both `POST /documents` uploads and `POST /inbound` requests have a 10 MiB limit
(10 × 1,024 × 1,024 bytes), counted while the body is read.
The limit includes multipart overhead for uploads and JSON/base64 overhead for webhooks.
The webhook allows 60 requests per minute per directly connected IP,
per API process (`DOCKET_WEBHOOK_RATE_LIMIT`);
forwarded-IP headers are ignored. Sending the same message and attachment again
returns the existing document id and records nothing new. The same file bytes
from a different message also return the existing document. A NUL character in
`message_id`, `from` or a file name fails with 422.

## Reviewer access and routes

Set `DOCKET_REVIEW_TOKEN` to a long random secret in the API environment.
The CLI reads the same variable.
Every route except `POST /inbound` and `GET /healthz` requires this header:

```http
Authorization: Bearer <the value of DOCKET_REVIEW_TOKEN>
```

An unset, empty or whitespace-only token makes protected routes return 503.
A missing or invalid Bearer token returns 401.
The root page presents token entry after a 401.
It keeps the token in session storage for that browser tab.
The page fetches scripts, styles and PDF previews with the authorization header.
A 401 clears its saved token; a server or network failure keeps it for retry.

| Route | Purpose |
| --- | --- |
| `GET /` | Open the review page |
| `GET /review` | List act-mode documents waiting for review, oldest first |
| `GET /documents/{id}` | Read status, reason and the event trace |
| `GET /documents/{id}/file` | Read the PDF preview with reviewer authorization |
| `POST /review/{id}/approve` | Send JSON with `record_id` to approve a known record |
| `POST /review/{id}/dismiss` | Send JSON with a nonempty `reason` to dismiss |

A terminal document or duplicate, late or invalid review action returns 409.
Approving a known record files the document to that record.
The model suggestion cannot approve it for the reviewer.

## Storage and the ledger

The workflow uses storage and the ledger for each document.

- **Storage** (`docket/core/storage.py`) writes files to S3-compatible storage under
  `<building>/<record_id>/<category>/<sha256[:16]>.pdf`.
  The filename uses the first 16 hexadecimal characters of the file's SHA-256 digest.
  Writing the same bytes to the same key again does nothing, so retries never write twice. Different bytes under an
  existing key raise `StorageConflict`. Key parts must match `[A-Za-z0-9_-]+`.
- **Ledger** (`docket/core/ledger.py`) stores each document's status in Postgres and
  logs every change as an event. An event with the same step for the same document
  is written once. Status moves only along an allowed list, and a final status such
  as `filed` or `rejected` never changes.
- A document runs in one of two modes. In `act` mode it can be filed or wait for
  review. In `observe` mode it only records what would have happened: `would_file`
  or `would_review` replace filing and review, and `rejected` and `failed` still apply.

## Model gateway

`docket/llm/` calls a language model. The model
picks among the shortlisted records. The response schema and import checks refuse
picks outside that shortlist. Automatic filing uses the rules' top candidate;
the model's record suggestion does not affect its score, tier or filing decision.

- **Provider.** `DOCKET_MODEL_PROVIDER` is `openrouter` (the default) or `anthropic`.
  The defaults, `openrouter` with `openai/gpt-4o-mini`, replay the saved v0.1
  answers. Live answers use `anthropic` with `DOCKET_MODEL=claude-haiku-5-5` and
  the key in `ANTHROPIC_API_KEY`. OpenRouter uses `OPENROUTER_API_KEY`. Keys come
  from the environment or the gitignored `.env`.
- **Saved answers.** Each provider has its own file, so live and simulated answers
  never share one: `evals/recordings/anthropic.jsonl` or `evals/recordings/calls.jsonl`.
  `DOCKET_REPLAY_PATH` overrides it. A saved answer is found by a key that includes
  the model name, so answers replay only under the model they were saved for.
- **Modes.** `live` calls the model. `record` calls it and saves the answer, and
  accepts only the synthetic corpus. A request that already has a saved answer,
  readable or not, is replayed in `record` mode instead of sent again. `replay`
  returns saved answers with no network or key, so tests are repeatable. `export`
  writes each request that has no saved answer to a file, and `import` checks
  supplied answers and saves them.
- **Budget.** Before each call, Docket reserves the worst-case cost against a run cap
  and a daily cap kept in Postgres. In live use each document has its own run cap
  (`DOCKET_RUN_BUDGET_USD`, default $2.00); an evaluation run shares one. An
  evaluation refuses a run budget above `DOCKET_RUN_BUDGET_CAP_USD` (default $5.00).
  The daily cap is shared by all calls and defaults to $1.50 (`DOCKET_DAILY_BUDGET_USD`).
  After the call it charges the real cost: for Anthropic, the tokens the API reports
  times the prices in [config/model_prices.yaml](../config/model_prices.yaml).
- **Retries.** A rate limit is not billed, and on Anthropic neither is a server error
  or a refused connection. The gateway retries these after 1, 2, 4 and 8 seconds.
  After the fourth retry an Anthropic call raises `NoAnswer`. A connection that fails
  after the request was sent, such as a read timeout, may have been billed.
  It raises `MaybeBilled`, keeps its worst-case charge and is never resent.
  Each Anthropic request times out after 15 seconds.
  The classify and choose steps get 120 seconds in Temporal, longer than a call with
  every retry can take, so the deadline never cuts a call off and starts it again.
- **Unreadable answers.** A refusal, an answer cut off at the token limit, text that
  is not JSON, or JSON that fails the schema raises `UnreadableAnswer`, a kind of
  `ParseError`. The API bills these, so they are charged and saved with their reason
  and raw text. Replaying one raises the same error.
- **Schema fallback.** Anthropic calls send the schema as structured output. If the
  API refuses the schema, the process asks for JSON in the prompt from then on. Each
  live answer records which of the two it used.
- **Failures in the workflow.** None of these errors is retried by Temporal. In
  choose, the workflow records no suggestion and the error reason (`parse_error`,
  `no_answer` or `maybe_billed`), then scores with rules. In classify, an unreadable
  answer sends the document to review; `no_answer` or `maybe_billed` ends it failed.
- **Refusal category.** When the API's safety filter refuses, it names a category,
  such as `bio`. The recording, the classify event and the report keep it.

`docket spike-models` checks that the configured model keeps to a different list
of allowed ids on each call. It makes two real calls, so it needs the provider's
key. It also needs Postgres with the tables created, because the daily cap is kept
there:

```sh
cp .env.example .env        # then set ANTHROPIC_API_KEY and the provider in .env
docker compose up -d
uv run alembic upgrade head
uv run docket spike-models
```

For Anthropic the spike has a $0.05 budget. It writes
`docs/adr/008-spike-result.json` and the Spike section of
[ADR 008](adr/008-real-model.md). For OpenRouter the budget is $0.50, and the result
goes to [ADR 001](adr/001-model-enum-spike.md).

## Test corpus

`corpus/seed-7/` and `corpus/seed-11/` each hold 180 synthetic documents and a
`labels.jsonl` file with the right answer for each one. The cases cover clean
documents, single-signal documents, conflicts, near-miss addresses, injected
instructions, graded name and address errors, exact duplicates, a very long
document and broken files (no text, corrupt, encrypted, zero bytes, not a PDF).

The same seed always gives the same text and labels:

```sh
uv run docket corpus generate --seed 7 --output /tmp/seed-7
```

## Run the checks

The checks need Node.js 22.12+ and Docker Compose as well as uv.
The tests need Postgres, Temporal and S3 storage. `compose.yaml` runs Postgres, a
Temporal dev server, SeaweedFS (S3-compatible storage), the API and the worker.
Every port listens on 127.0.0.1 only.

```sh
docker compose up -d
./verify.sh
```

| Service  | Address                                 |
| -------- | --------------------------------------- |
| Postgres | `127.0.0.1:55432`                       |
| Temporal | `127.0.0.1:7233`, web UI on `127.0.0.1:8233` |
| API      | `http://127.0.0.1:8010`                |
| S3       | `http://127.0.0.1:9000`                 |

`verify.sh` runs ruff and pyright (strict on `docket/core`), checks the exchange
archive, builds the Vite review page and Docker images, and creates the database
tables. Then it runs pytest. That includes Playwright browser tests against
their own API and worker containers in replay mode: one approves a suggested record
end to end, and one checks that a script-like file name and sender show as plain
text. Nine more check token retention after access and asset failures. Chromium runs in a separate test image, not in the API or worker images.
`verify.sh` fails if any test is skipped, or if branch coverage of a core module
falls below its floor in `scripts/check_coverage.py`. Last, it reproduces the
held-out report and runs detect-secrets, which also scans the archives' contents.
The scan skips the local `.env`, which holds keys, and `verify.sh` fails if git
does not ignore `.env`.
CI runs `./verify.sh` and `./verify.sh --fault`.

`./verify.sh --fault` also runs the fault suite in `tests/fault/`.
`docket/failpoints.py` stops a process at a named point when `DOCKET_FAILPOINT`
names it. The five tests crash the worker in the choose step and right after
storage, crash the API after it saves the raw file and after it creates the
document row, and stop storage while a restarted worker is running. Each test checks that the
document is never filed twice and still reaches a final status.

## Layout

| Path                      | What it holds                                         |
| ------------------------- | ----------------------------------------------------- |
| `docket/core/`            | Extraction, shortlist, scoring, storage and ledger    |
| `docket/llm/`             | Model gateway, replay store, exchange archive, budget |
| `docket/workflows.py`     | The per-document Temporal workflow                    |
| `docket/activities.py`    | The workflow's steps, run by `docket/worker.py`       |
| `docket/api.py`           | Upload, webhook, status and review endpoints          |
| `docket/webhook.py`       | Webhook signature check, size limit and rate limit    |
| `docket/review_access.py` | Reviewer token check and the token entry page         |
| `docket/evaluation.py`    | Evaluation runs and reports                           |
| `docket/cli.py`           | The `docket` command                                  |
| `web/`                    | The review page and its Playwright tests              |
| `config/`                 | Records, categories and scoring weights               |
| `migrations/`             | Alembic migrations for the Postgres tables            |
| `corpus/`                 | Committed synthetic documents and labels              |
| `evals/`                  | Reports, saved answers and the exchange archive       |
| `docs/adr/`               | Decision records                                      |
