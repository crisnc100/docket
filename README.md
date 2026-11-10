# docket

Docket files incoming documents to the record they belong to. A document arrives,
Docket reads its text, works out which known record it is about, stores the file and
logs every step. When the match is uncertain, the document goes to a person for
review instead of being guessed.

A *record* here is one property unit, such as `UNIT-0001` at "1010 Cedar Street Apt 1".
The project ships with 200 synthetic units in `config/records.csv`, so no real
personal data is involved.

The matching code runs in a durable Temporal workflow. Temporal saves progress so a
worker can resume after a crash. The review UI and inbound webhook are not built yet.

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
3. **Score.** Each signal has a weight in `config/scoring.json`. The weights are
   untuned defaults. Name and address strengths vary with match quality. The score
   is between 0 and 1 and falls in a tier: `none`,
   `medium` or `strong`.
   - `strong` needs at least two different signals. One signal alone, even a record
     id, never reaches `strong`.
   - A conflict sets the score to 0 and sends the document to review.
   - Only a `strong` match may be filed without a person.

Requiring two signals is the main defence against injected text. A document that
just names a record id cannot file itself.

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
  review. In `observe` mode it only records what would have happened, ending as
  `would_file` or `would_review`.

## Model gateway

`docket/llm/` calls a language model through OpenRouter. The model
picks among the shortlisted records. Scoring refuses a pick that is not on the
shortlist (`InvalidChoice`), and the score above still decides whether anything files.

- **Modes.** `live` calls the model. `record` calls it and saves the answer, and
  accepts only the synthetic corpus. `replay` returns saved answers with no network
  or key, so tests are repeatable.
- **Budget.** Before each call, Docket reserves the worst-case cost against a per-run
  cap and a daily cap kept in Postgres. The daily cap defaults to $1.50
  (`DOCKET_DAILY_BUDGET_USD`). After the call it charges the real cost.
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
instructions, graded name and address errors, exact duplicates, a very long document and broken files (no text,
corrupt, encrypted, zero bytes, not a PDF).

The same seed always gives the same text and labels:

```sh
uv run docket corpus generate --seed 7 --output /tmp/seed-7
```

## Run the checks

The tests need Postgres and S3 storage. `compose.yaml` runs Postgres, a Temporal dev
server and SeaweedFS (S3-compatible). Every port listens on 127.0.0.1 only.

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

`verify.sh` runs ruff and pyright (strict on `docket/core`), creates the database
tables, then runs pytest and detect-secrets. It fails if any test is skipped, or if
branch coverage of a core module falls below its floor in
`scripts/check_coverage.py`. It also builds the Docker images and reproduces the
held-out replay report. `./verify.sh --fault` includes five tests that crash workers
or interrupt storage and check that each filing still happens once.

## Workflow and evaluation

`POST /documents` stores a document and starts its workflow.
`uv run docket worker` runs the worker. `GET /documents/{id}` reads its result.
The workflow extracts text, shortlists records, classifies the document, asks the
model to choose a candidate, scores that choice and files or requests review.
An empty shortlist bypasses both model calls. A strong tier and the configured
filing threshold are both required to file. A conflict always goes to review.
`observe` mode records what would happen without filing or waiting for a reviewer.
`docket reconcile` finds interrupted submissions and abandoned workflows.

The gateway can export unanswered requests and import operator-supplied answers.
Imported answers must match each request's response schema. Their provenance names
both the simulator and the model it stands in for. Replay makes no external calls.
See [docs/rerecord.md](docs/rerecord.md) for the commands.

The saved reports use simulated answers: 422 Haiku answers and 18 Opus answers.
Opus supplied the 18 answers that Haiku's safety filter blocked on synthetic text.
These are not OpenRouter measurements. The filing threshold is 0.5857142, selected
on seed 7 only. Weights and tier thresholds remain untuned defaults.
Seed 11 is held out and selects no settings.

The [seed-7 report](evals/reports/2026-11-09-seed-7.md) and
[seed-11 report](evals/reports/2026-11-09-seed-11.md) describe the saved run before
the tenant-name corrections merged from core. They require replay against the
merged corpus before those results apply to this tree.
In that saved run, model choice changed no filing decision or filed record on
either seed. It changed suggested records for documents already going to review.
The model showed no filing gain over the rules-only baseline, which chooses the
highest-scored candidate without a model.

The saved held-out run had zero wrong filings, 90% clean filing, no filings to
injected ids and recall of 36/39, equal to the baseline.
Recall is the share of expected filings found correctly.
Its eight-point threshold curve had six distinct precision/rate pairs.
Precision is the share of automatic filings with the expected record and category.
It was 100% when there were filings; higher thresholds reduced the share filed.
Category accuracy was 124/140 for Docket and 132/140 for the baseline on both seeds.
Category accuracy is the share of non-rejected documents with the expected category.
Eight documents per seed with no candidates received misc without a model call.
These numbers describe the saved reports, not a measurement of the merged corpus.

`docket shadow-diff --report <report.json>` compares Docket with the baseline and
checks each disagreement against the corpus labels.

## Layout

| Path            | What it holds                                         |
| --------------- | ----------------------------------------------------- |
| `docket/core/`  | Extraction, shortlist, scoring, storage and ledger    |
| `docket/llm/`   | Model gateway, replay store and budget                |
| `docket/cli.py` | The `docket` command                                  |
| `config/`       | Records, categories and scoring weights               |
| `migrations/`   | Alembic migrations for the Postgres tables            |
| `corpus/`       | Committed synthetic documents and labels              |
| `docs/adr/`     | Decision records                                      |

## License

Apache-2.0. See [LICENSE](LICENSE).
