# Docket

Incoming leases, invoices and notices need to reach the right property unit.
Docket reads a PDF, matches it to known units, and files it when the evidence agrees.
Uncertain matches wait for a person. Every step has a trace.
A *record* is one property unit, such as `UNIT-0001`.
The shipped data is synthetic: 200 units and two sets of 180 test documents.

Rules decide automatic filing. A language model can suggest a record to a reviewer.
It never decides which record is filed automatically.
The model is Claude Haiku 5.5 on the Anthropic API, measured live.
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
An unreadable category answer, such as a refusal, sends the document to review.
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

**These are live answers from claude-haiku-5-5 on the Anthropic API, recorded on 2026-11-13.**
One pass asked each of the 440 requests over seeds 7 and 11 once.
The [report](evals/reports/2026-11-13-seed-11.md) gives every figure below.
[ADR 008](docs/adr/008-real-model.md) has the gates and the predictions made before the pass.
The v0.1 simulated answers are kept as [history](evals/history/simulated/README.md).

**The live pass missed two of its four gates.** The API's safety filter refused both
calls on all 20 `very_long` test documents: 40 of 440 requests (9.1%), against a limit
of 8 (2%). Its category was `bio`. Those documents hold about 8,000 characters of random
synthetic words. A resend was refused again. No injection document was refused.
Docket sends a refused document to review instead of failing it, so nothing is filed
wrongly, but the ten on seed 11 that the rules would file wait for a person.
That misses the recall floor. [Failure 0017](docs/failures/0017-very-long-refusal.md)
explains it.

| Gate | Result |
| --- | --- |
| G1: at most 2% of answers unreadable | **Missed:** 40/440, 9.1% [6.7–12.1], all refusals |
| G2: at most 2% with no answer | Passed: 0/440 |
| G3: seed-11 floors | **Missed on recall:** 28/39 against the baseline's 38/39. No wrong filings, all 10 clean documents filed, no filing to an injected id |
| G4: spend under $5 | Passed: $0.0293 for the pass |

A *gold label* is the expected answer supplied with a test document.
Precision is correct filings divided by all automatic filings.
Recall is correct filings divided by the 39 documents labelled for filing.

| Measure | Docket | Rules only |
| --- | --- | --- |
| Filing precision | 28/28, 100% [87.9–100] | 38/38, 100% [90.8–100] |
| Filing recall | 28/39, 71.8% [56.2–83.5] | 38/39, 97.4% [86.8–99.5] |
| Share filed automatically | 28/180, 15.6% [11.0–21.6] | 38/180, 21.1% [15.8–27.6] |
| Share sent to review | 112/180, 62.2% [55.0–69.0] | 102/180, 56.7% [49.4–63.7] |
| Pipeline category accuracy, among 140 not rejected | 116/140, 82.9% [75.8–88.2] | 132/140, 94.3% [89.1–97.1] |
| Correct record on review items | 43/102, 42.2% [33.0–51.9] | 62/102, 60.8% [51.1–69.7] |

Brackets give 95% Wilson intervals: uncertainty ranges for the measured proportions.
They do not establish accuracy on real incoming documents.
The 10 filings Docket gives up are the refused `very_long` documents; it files nothing
the rules would not. The other 40 documents were rejected.
Of the 120 documents that reached the model, the category was right on 112.
Every readable category answer was right (110 of 110); the ten refused fell back to misc,
which matched gold on two. The other 16 category misses came from empty shortlists,
assigned misc without a model call.
The suggestion comparison leaves out the 10 review documents whose suggestion was refused.
Suggesting none is correct when gold names no record.

| Cost and speed | Seed 11 | Whole pass |
| --- | --- | --- |
| Model spend | $0.0147 | $0.0293 (440 calls) |
| Per document that reached the model | $0.000122 | $0.000122 |
| Per document not rejected | $0.000105 | $0.000105 |
| Per document overall | $0.000081 | $0.000081 |
| Latency per call, median / 95th percentile | 1.29 s / 2.69 s | 1.33 s / 2.56 s |

Cost is tokens times the [price snapshot](config/model_prices.yaml) of 2026-11-12.
The [cost summary](evals/cost/2026-11-13-claude-haiku-5-5.json) rebuilds it from the recordings.
Every answer used structured output; the schema fallback was never needed.

The threshold curve below comes directly from the committed report.
Each cell gives a percentage and its 95% Wilson interval.
The auto-file rate is the share of all 180 documents selected for filing.

| Threshold | Docket precision | Rules precision | Docket auto-file rate | Rules auto-file rate |
| --- | --- | --- | --- | --- |
| 0.5500000 | 100.0% [88.3–100.0] | 100.0% [91.0–100.0] | 16.1% [11.5–22.2] | 21.7% [16.3–28.2] |
| 0.6142857 | 100.0% [87.9–100.0] | 100.0% [90.8–100.0] | 15.6% [11.0–21.6] | 21.1% [15.8–27.6] |
| 0.6785714 | 100.0% [86.7–100.0] | 100.0% [90.1–100.0] | 13.9% [9.6–19.7] | 19.4% [14.3–25.8] |
| 0.7428571 | 100.0% [86.7–100.0] | 100.0% [90.1–100.0] | 13.9% [9.6–19.7] | 19.4% [14.3–25.8] |
| 0.8071429 | 100.0% [86.7–100.0] | 100.0% [90.1–100.0] | 13.9% [9.6–19.7] | 19.4% [14.3–25.8] |
| 0.8714286 | 100.0% [85.1–100.0] | 100.0% [89.3–100.0] | 12.2% [8.2–17.8] | 17.8% [12.9–24.0] |
| 0.9357143 | 100.0% [84.5–100.0] | 100.0% [89.0–100.0] | 11.7% [7.8–17.2] | 17.2% [12.4–23.4] |
| 1.0000000 | — | — | 0.0% [0.0–2.1] | 0.0% [0.0–2.1] |

At threshold 1.0 there are no filings, so precision is undefined.
The eight thresholds produce six distinct precision/rate pairs for each system.
At every threshold Docket's rate is lower by the refused `very_long` documents.
The [JSON report](evals/reports/2026-11-13-seed-11.json) contains unrounded values,
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
- Documents are synthetic. The live figures measure Haiku 5.5 on this test set, not on real mail.
- Haiku's safety filter refuses the synthetic `very_long` documents (category `bio`), so the
  live pass missed its unreadable-answer and recall gates. Those documents go to review.
- Conflicting evidence and single-signal matches go to review. This protects filing precision
  at the cost of more human work.
- An unreadable category answer sends the document to review. A category call with no
  answer at all still fails the document.
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
- [Evidence archive](evals/recordings/README.md): live requests, answers, tokens and provenance.
- [Build plan](builds/v01/plan.md): acceptance criteria and approved changes.

Apache-2.0. See [LICENSE](LICENSE).
