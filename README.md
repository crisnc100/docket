# Docket

Incoming leases, invoices and notices need to reach the right property unit.
Docket reads a PDF, matches it to known units, and files it when the evidence agrees.
Uncertain matches wait for a person. Every step has a trace.
A *record* is one property unit, such as `UNIT-0001`.
The shipped data is synthetic: 200 units and two sets of 180 test documents.

Rules decide automatic filing. A language model can suggest a record to a reviewer.
It never decides which record is filed automatically.
In the held-out test, its suggestions were less accurate than the rules.

```mermaid
flowchart TD
    A[PDF upload or signed email webhook] --> B[Save raw bytes and deduplicate]
    B --> C[Temporal workflow: save progress across crashes]
    C --> D[Read PDF text]
    D -->|Unreadable or unsupported| R[Rejected with a reason]
    D --> E[Find up to five candidate records]
    E --> F[Model: category and optional record suggestion]
    E --> G[Rules: score matching evidence]
    F --> G
    G -->|Two signals, threshold met, no conflict| H[File to object storage]
    G -->|Uncertain| I[Review page: PDF, rules and suggestion]
    I -->|Approve a known record| H
    I --> J[Dismiss or expire with a reason]
    C -. Other errors .-> K[Failed with a reason]
```

A *signal* is evidence such as a matching address or tenant name.
Two independent signals must agree before a document can file automatically.
A failed record suggestion does not block the rules.
Temporal runs the steps and saves progress so a worker can resume after a crash.

## Quickstart

Install [uv](https://docs.astral.sh/uv/), then run from this repository:

```sh
uv sync
uv run docket score corpus/seed-7/doc-0001-clean.pdf
```

This prints candidate records, scores and a filing decision.
It needs no model key or running services and writes no filing.

For the service and review page, also install Docker Compose:

```sh
export DOCKET_REVIEW_TOKEN="$(openssl rand -hex 32)"
docker compose up -d --build --wait
uv run docket submit corpus/seed-11/doc-0075-decoy_near_miss_address.pdf
```

Open [the local review page](http://127.0.0.1:8010/) and enter the token.
The sample waits for review. It uses saved model answers, with no network model call.
The token stays in that browser tab's session storage.
The page shows the PDF, why it needs review, candidate scores, the rules' top pick
and the model's suggestion. Approve either, choose another candidate, or dismiss.
`uv run docket status <document-id>` prints the trace.

Upload with `POST /documents`, or send a signed email payload to `POST /inbound`.
Both have a **10 MB limit (10 MiB, 10 × 1,024 × 1,024 bytes)** on the whole request,
including upload or encoding overhead. Oversize requests return 413.
Every route except the webhook and health check needs a reviewer Bearer token.
The webhook uses a shared-secret signature and a timestamp within five minutes.
See [operations](docs/operations.md) for headers, routes, settings and services.

## Held-out results

Seed 7 chose the score threshold, 0.5857142. A seed fixes the generated test data.
Seed 11 was *held out*: none of its results selected settings.
The *rules-only baseline* takes the highest-scored candidate without a model.
It uses the same threshold and two-signal rule as Docket.

**These are simulated model answers, not OpenRouter measurements.**
Haiku supplied most answers; Opus supplied the requests Haiku refused.
The [report](evals/reports/2026-11-11-seed-11.md) names the sources and counts.
No real-provider quality, schema compliance or cost has been measured.

At the default threshold, both systems file the same 38 of 180 documents.
A *gold label* is the expected answer supplied with a test document.
Precision is correct filings divided by all automatic filings.
Recall is correct filings divided by the 39 documents labelled for filing.

| Measure | Docket | Rules only |
| --- | --- | --- |
| Filing precision | 38/38, 100% [90.8–100] | 38/38, 100% [90.8–100] |
| Filing recall | 38/39, 97.4% [86.8–99.5] | 38/39, 97.4% [86.8–99.5] |
| Share filed automatically | 38/180, 21.1% [15.8–27.6] | 38/180, 21.1% [15.8–27.6] |
| Share sent to review | 102/180, 56.7% [49.4–63.7] | 102/180, 56.7% [49.4–63.7] |
| Category accuracy, among 140 not rejected | 124/140, 88.6% [82.2–92.8] | 132/140, 94.3% [89.1–97.1] |
| Correct record on review items | 44/102, 43.1% [33.9–52.8] | 62/102, 60.8% [51.1–69.7] |

Brackets give 95% Wilson intervals: uncertainty ranges for the measured proportions.
They do not establish accuracy on real incoming documents.
There were zero wrong filings and zero filings to injected record ids.
All 10 clean documents filed; all four held-out floors pass.
The other 40 documents were rejected.

Only documents sent to review count in the suggestion comparison.
Suggesting none is correct when gold names no record.
Suggestions differ from the rules on 79 review documents and change no filing decision.

The threshold curve below comes directly from the committed report.
Each cell gives a percentage and its 95% Wilson interval.
The auto-file rate is the share of all 180 documents selected for filing.

| Threshold | Docket precision | Rules precision | Docket auto-file rate | Rules auto-file rate |
| --- | --- | --- | --- | --- |
| 0.5500000 | 100.0% [91.0–100.0] | 100.0% [91.0–100.0] | 21.7% [16.3–28.2] | 21.7% [16.3–28.2] |
| 0.6142857 | 100.0% [90.8–100.0] | 100.0% [90.8–100.0] | 21.1% [15.8–27.6] | 21.1% [15.8–27.6] |
| 0.6785714 | 100.0% [90.1–100.0] | 100.0% [90.1–100.0] | 19.4% [14.3–25.8] | 19.4% [14.3–25.8] |
| 0.7428571 | 100.0% [90.1–100.0] | 100.0% [90.1–100.0] | 19.4% [14.3–25.8] | 19.4% [14.3–25.8] |
| 0.8071429 | 100.0% [90.1–100.0] | 100.0% [90.1–100.0] | 19.4% [14.3–25.8] | 19.4% [14.3–25.8] |
| 0.8714286 | 100.0% [89.3–100.0] | 100.0% [89.3–100.0] | 17.8% [12.9–24.0] | 17.8% [12.9–24.0] |
| 0.9357143 | 100.0% [89.0–100.0] | 100.0% [89.0–100.0] | 17.2% [12.4–23.4] | 17.2% [12.4–23.4] |
| 1.0000000 | — | — | 0.0% [0.0–2.1] | 0.0% [0.0–2.1] |

At threshold 1.0 there are no filings, so precision is undefined.
The eight thresholds produce six distinct precision/rate pairs.
Raising the threshold loses filings here without a measured precision gain.
The [JSON report](evals/reports/2026-11-11-seed-11.json) contains unrounded values,
per-case results and each review suggestion.
[Failure notes](docs/failures/README.md) show missed filings and wrong suggestions.

## Crash and retry checks

Five fault tests stop real Compose processes and check recovery.
A failpoint is a named place where a test deliberately exits a process.
Each process-crash test checks exit code 137 and the fired-point log.

| Fault | Checked outcome |
| --- | --- |
| Worker exits after storage, before the database write | One filing object, one filing row and one filed event after restart |
| Worker exits in choose | Same single-filing checks after restart |
| API exits after the raw upload | Resubmission recovers one document and one filing |
| API exits after the document row, before workflow start | Reconciliation starts the missing workflow; one filing |
| Storage goes offline during worker recovery | A real connection error, then one filing after storage returns |

The worker-crash cases also submit three requests concurrently and resubmit after filing
under a different message id. They still produce one document and one filing.
Steps may run again. Database uniqueness and repeat-safe storage writes prevent duplicate effects.
The suite does not measure outages longer than the retry window or production failover.
See [ADR 007](docs/adr/007-temporal-recovery.md) for the measured scope.

## Limits

- Deduplication uses exact bytes. Near-duplicates with different bytes remain separate documents.
- There is no optical character recognition (OCR), which reads text from page images.
  Scans without a text layer go to review.
- Answers are simulated and documents are synthetic. Neither demonstrates real-provider quality.
- Conflicting evidence and single-signal matches go to review. This protects filing precision
  at the cost of more human work.
- A category-call failure still fails the document. Only the record suggestion is optional.
- Matching has field-size budgets. Over-budget input goes to review rather than losing evidence
  through silent truncation. [Operations](docs/operations.md#how-a-document-is-matched) gives the limits.
- Local Compose uses a Temporal development server, one API process and one shared reviewer token.
  This is not a measured production deployment or an individual-user permissions system.

## Check and explore

Checks also need Node.js 22.12+. Start Compose first:

```sh
./verify.sh
./verify.sh --fault
```

Both enforce zero skipped tests, coverage floors, formatting, type checks, browser checks,
Docker builds, the saved-answer archive and secret scanning.
They also replay seed 11 and compare it with the saved results, without a model call.

- [Product brief](docs/product-brief.md): users, scope and measures.
- [Decision records](docs/adr/README.md): choices and measured limits.
- [Build log](docs/build-log.md): dated decisions and what review caught.
- [Operations](docs/operations.md): matching, API, storage, model budgets and local services.
- [Replay instructions](docs/rerecord.md): reproduce the reports or record real-provider answers.
- [Evidence archive](evals/recordings/README.md): original requests, answers and provenance.
- [Build plan](builds/v01/plan.md): acceptance criteria and approved changes.

Apache-2.0. See [LICENSE](LICENSE).
