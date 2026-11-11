# docket

Docket files incoming documents to the record they belong to. A document arrives,
Docket reads its text, works out which known record it is about, stores the file and
logs every step. When the match is uncertain, the document goes to a person for
review instead of being guessed.

A *record* here is one property unit, such as `UNIT-0001` at "1010 Cedar Street Apt 1".
The project ships with 200 synthetic units in `config/records.csv`, so no real
personal data is involved.

The matching code runs in a durable Temporal workflow. Temporal saves progress so a
worker can resume after a crash.

## Try it

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for you.
Run every command from the repository root.

```sh
uv sync
uv run docket score corpus/seed-7/doc-0001-clean.pdf
```

`docket score` reads one PDF, lists the records it could belong to and scores them.
It makes no model call and needs no services. It prints JSON like this (trimmed):

```json
{
  "status": "scored",
  "chosen_record_id": "UNIT-0083",
  "score": "0.9500000",
  "tier": "strong",
  "conflict": false,
  "would_auto_file": true,
  "reason": "threshold_met"
}
```

Try a few others from `corpus/seed-7/`:

- `doc-0003-address_only.pdf`: one signal only, so it scores `medium` and goes to review.
- `doc-0010-injection.pdf`: the text tells the reader to file it to another unit. The
  two units conflict, so it goes to review.
- `doc-0013-corrupt.pdf`: prints `"status": "rejected"` with a reason and exits with code 1.

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
hash and starts one Temporal workflow for the document. Uploading the same bytes
again returns the same document id. `uv run docket worker` runs the worker.
`GET /documents/{id}` shows the status, the reason and every logged step.

The workflow runs these steps:

1. Extract the text, as above.
2. Shortlist candidate records, as above.
3. Ask the model for a category, then ask it to choose one shortlisted record or
   `none`. An empty shortlist skips both calls and the category is `misc`.
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
[ADR-002](docs/adr/002-review-workflow-patching.md) explains how to change the
workflow while reviews are still waiting.

## Inbound webhook

Set `DOCKET_WEBHOOK_SECRET` to a shared secret before using `POST /inbound`.
Send JSON with `message_id`, `from`, and an `attachments` list; each attachment has
`filename` and `content` (base64 bytes). Sign the exact body bytes:
`HMAC-SHA256(secret, timestamp + "." + raw_body)`. Send the Unix timestamp in
`X-Docket-Timestamp` and the hex digest prefixed with `sha256=` in
`X-Docket-Signature`. Timestamps more than five minutes in either direction fail
with 401. An unset secret disables webhook admission.

Requests are limited to 10 MiB while reading, including JSON/base64 or multipart
overhead. The webhook allows 60 requests per minute per directly connected IP,
per API process (`DOCKET_WEBHOOK_RATE_LIMIT`); forwarded-IP headers are ignored.
A repeated message and identical attachment returns the existing document and
adds no source. Content deduplication also applies across different messages.

`uv run docket submit <pdf>` uploads to the local API. `uv run docket status <id>`
prints its trace; both accept `--api-url`. The PDF preview is proxied at
`/documents/<id>/file`. Open `http://127.0.0.1:8010/` for the review queue.
The page shows the suggestion beside the rules' top candidate, lets you approve
either or pick another candidate, and supports dismissing with a reason.

Set `DOCKET_REVIEW_TOKEN` in the API environment and the CLI environment. Without
it, reviewer routes return 503. Every route except `POST /inbound` and
`GET /healthz` requires `Authorization: Bearer <token>`. The page asks for the token
and keeps it in sessionStorage for that tab. PDF previews use authenticated fetches
and blob URLs; the token never goes in a URL.

## Storage and the ledger

The workflow uses storage and the ledger for each document.

- **Storage** (`docket/core/storage.py`) writes files to S3-compatible storage under
  `<building>/<record_id>/<category>/<hash>.pdf`. Writing the same bytes to the same
  key again does nothing, so retries never write twice. Different bytes under an
  existing key raise `StorageConflict`. Key parts must match `[A-Za-z0-9_-]+`.
- **Ledger** (`docket/core/ledger.py`) stores each document's status in Postgres and
  logs every change as an event. An event with the same step for the same document
  is written once. Status moves only along an allowed list, and a final status such
  as `filed` or `rejected` never changes.
- A document runs in one of two modes. In `act` mode it can be filed or wait for
  review. In `observe` mode it only records what would have happened: `would_file`
  or `would_review` replace filing and review, and `rejected` and `failed` still apply.

## Model gateway

`docket/llm/` calls a language model through OpenRouter. The model
picks among the shortlisted records. Scoring refuses a pick that is not on the
shortlist (`InvalidChoice`). Automatic filing uses the rules' top candidate;
the model's record suggestion does not affect its score, tier or filing decision.

- **Modes.** `live` calls the model. `record` calls it and saves the answer, and
  accepts only the synthetic corpus. `replay` returns saved answers with no network
  or key, so tests are repeatable. `export` writes each request that has no saved
  answer to a file, and `import` checks supplied answers and saves them.
- **Budget.** Before each call, Docket reserves the worst-case cost against a run cap
  and a daily cap kept in Postgres. In live use each document has its own run cap
  (`DOCKET_RUN_BUDGET_USD`, default $2.00); an evaluation run shares one. The daily
  cap is shared by all calls and defaults to $1.50 (`DOCKET_DAILY_BUDGET_USD`).
  After the call it charges the real cost.
- **Bad output.** An answer that does not fit the schema raises `ParseError`, which
  is not retried. This also covers truncated and filtered responses.

`docket spike-models` checks that the configured model keeps to a different list
of allowed ids on each call. It makes real calls, so it needs an OpenRouter key. It
also needs Postgres with the tables created, because the daily cap is kept there:

```sh
cp .env.example .env        # then set OPENROUTER_API_KEY in .env
docker compose up -d
uv run alembic upgrade head
uv run docket spike-models
```

See [docs/adr/001-model-enum-spike.md](docs/adr/001-model-enum-spike.md).

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

## Simulated evaluation

No OpenRouter key is available yet, so the model answers behind the reports are
simulated. Docket exported each model request to a file, and Claude Haiku answered
it in its place. Opus answered the few requests that Haiku's safety filter wrongly
blocked. The operator reports that each answer came from a fresh call that saw only
its own request. Every answer names the model that gave it and the model it stands
in for, and every report says its results are simulated.

`docket eval --seed N` runs a corpus in `observe` mode and compares Docket with a
rules-only baseline, which takes the highest-scored candidate without a model.
Seed 7 picked the filing threshold (`scripts/tune_threshold.py`). Seed 11 is held
out and picked nothing.

The held-out result ([seed-11 report](evals/reports/2026-11-11-seed-11.md)):

- 0 wrong filings, and no filings to a record id injected into the text.
- All 10 clean documents filed without a person.
- Recall of 38/39 (97.4%), equal to the baseline. Recall is the share of documents
  that should file and did file, to the right record.
- The model's choose step changes no filing decision in v0.1. Docket and the
  baseline file the same 38 documents. The choice changes only the record suggested
  to the reviewer, on 79 documents that go to review either way.
- Category accuracy is lower with the model: 124/140, against 132/140 for the
  baseline.

Among the 102 seed-11 documents sent to review, the model's suggestion
was correct on 44/102 (43.1%); the rules' top candidate was correct on
62/102 (60.8%). This includes a correct abstention when gold names no
record and excludes rejected documents. The report lists every compared document
and Wilson intervals. These are the existing simulated recordings, replayed with
no new model answers.

`verify.sh` fails unless the newest seed-11 report is a measured pass that replay
reproduces exactly (`scripts/reproduce_eval.py`). `docket eval` checks each place
where Docket and the baseline disagree against the corpus labels and stores that in
the report. `docket shadow-diff` saves that comparison as `<report>-shadow.json`. It
reads the newest seed-11 report unless you pass `--report`.

The evidence is one committed archive, `evals/recordings/exchange.jsonl.gz`. It
holds every request and answer exactly as exchanged, with their SHA-256 hashes and
provenance. Replay reads `evals/recordings/calls.jsonl`, and verify checks every
saved answer there against the archive. The working folders `evals/pending/` and
`evals/answers/` are not committed. See
[evals/recordings/README.md](evals/recordings/README.md) for the archive format and
answer counts, [docs/rerecord.md](docs/rerecord.md) to rerun the simulation or
record with a real key, and
[ADR-004](docs/adr/004-simulated-model-measurements.md) for why simulated answers
were used.

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
tables. Then it runs pytest, including the Playwright review and XSS tests against
isolated Compose API and worker containers in replay mode. Chromium runs in a
separate test image; it is not installed in the API or worker images. It fails if any test is skipped, or if branch coverage of a core module
falls below its floor in `scripts/check_coverage.py`. Last, it reproduces the
held-out report and runs detect-secrets, which also scans the archive's contents.
CI runs `./verify.sh` and `./verify.sh --fault`.

`./verify.sh --fault` also runs the fault suite in `tests/fault/`.
`docket/failpoints.py` stops a process at a named point when `DOCKET_FAILPOINT`
names it. The five tests crash the worker in the choose step and right after
storage, crash the API after it saves the raw file and after it creates the
document row, and stop storage while a restarted worker is running. Each test checks that the
document is never filed twice and still reaches a final status.

## Layout

| Path                   | What it holds                                         |
| ---------------------- | ----------------------------------------------------- |
| `docket/core/`         | Extraction, shortlist, scoring, storage and ledger    |
| `docket/llm/`          | Model gateway, replay store, exchange archive, budget |
| `docket/workflows.py`  | The per-document Temporal workflow                    |
| `docket/activities.py` | The workflow's steps, run by `docket/worker.py`       |
| `docket/api.py`        | Upload, status and review endpoints                   |
| `docket/evaluation.py` | Evaluation runs and reports                           |
| `docket/cli.py`        | The `docket` command                                  |
| `config/`              | Records, categories and scoring weights               |
| `migrations/`          | Alembic migrations for the Postgres tables            |
| `corpus/`              | Committed synthetic documents and labels              |
| `evals/`               | Reports, saved answers and the exchange archive       |
| `docs/adr/`            | Decision records                                      |

## License

Apache-2.0. See [LICENSE](LICENSE).
