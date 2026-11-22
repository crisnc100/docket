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
uploads that never got one and marks abandoned documents `failed`. In beta it starts a
sandbox's workflow under the sandbox's lock, as a new write does, and skips a sandbox that
is closed or expired.
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
In local mode, every route except `POST /inbound`, `GET /healthz`, `GET /` and `GET /home/{file}` requires this header:

```http
Authorization: Bearer <the value of DOCKET_REVIEW_TOKEN>
```

An unset, empty or whitespace-only token makes protected routes return 503.
A missing or invalid Bearer token returns 401.
The review page at `/reviewer` presents token entry after a 401.
It keeps the token in session storage for that browser tab.
The page fetches scripts, styles and PDF previews with the authorization header.
A 401 clears its saved token; a server or network failure keeps it for retry.

| Route | Purpose |
| --- | --- |
| `GET /` | Open the public front page; no token needed |
| `GET /home/{file}` | Read the front page's own files; no token needed |
| `GET /reviewer` | Open the review page |
| `GET /review` | List act-mode documents waiting for review, oldest first |
| `GET /documents/{id}` | Read status, reason and the event trace |
| `GET /documents/{id}/file` | Read the PDF preview with reviewer authorization |
| `POST /review/{id}/approve` | Send JSON with `record_id` to approve a known record |
| `POST /review/{id}/dismiss` | Send JSON with a nonempty `reason` to dismiss |
| `GET /sandbox` | Beta only: the seconds the visitor's sandbox has left |
| `DELETE /sandbox` | Beta only: Delete now |

A terminal document or duplicate, late or invalid review action returns 409.
So does an action other than `approve` or `dismiss`.
A NUL character in a dismiss reason returns 422.
Both checks run before the request reaches Temporal, which keeps every update it
receives, even one the workflow then rejects.
Approving a known record files the document to that record.
The model suggestion cannot approve it for the reviewer.

## Hosted beta mode

Beta mode is a public demo that visitors use with no sign-up. Each browser gets a private
sandbox: nobody else can see or act on what it holds, and everything in it is deleted
within 55 minutes, or at once with "Delete now". Local use is unchanged. The README's
"Run the hosted beta" section covers `compose.prod.yaml`, `.env.beta.example` and the
steps on the host. [ADR 010](adr/010-sandboxes.md) records the choice and the
measurements behind the deletion.

Set `DOCKET_ENV=beta` to turn it on. These settings apply:

| Setting | What it does |
| --- | --- |
| `DOCKET_ENV` | `local` (the default) or `beta` |
| `DOCKET_ADDRESS_KEY` | The secret that keys the hashes the limits store in place of addresses, at least 32 bytes |
| `DOCKET_TRUST_PROXY` | `1` reads the client address from `X-Forwarded-For` |
| `DOCKET_BASE_URL` | The site's own origin, such as `https://demo.example`; every request that is not a `GET` must come from it |
| `DOCKET_REVIEW_TIMEOUT_SECONDS` | How long a held document waits for review; at most 3600 in beta, 3000 in the example settings |
| `SEAWEEDFS_MASTER_URL` | SeaweedFS's master, which removes deleted objects' bytes from its files on request |

**Sandboxes.** A visitor's first write, which in this version is a sample, creates the
sandbox. The answer sets the cookie `__Host-docket_sandbox`: `HttpOnly`, `Secure`,
`SameSite=Strict`, `Path=/`, and a `Max-Age` of the sandbox's 50 minutes. Its value is
128 random bits. The database stores only its SHA-256, so the database cannot hand a
cookie back. Later writes with the cookie go to the same sandbox. Only a request with
no cookie creates one, so a browser holds one sandbox and Delete now reaches all of it.
A cookie whose sandbox is closed, expired or gone gets 409, `{"detail": "That sandbox
ended. Send again.", "code": "sandbox_ended"}`, and the answer clears the cookie, so the
browser's next send starts a new sandbox. In its last five minutes a sandbox takes
nothing new, so nothing a visitor sends is deleted moments later: a send gets 409 with
the code `sandbox_ending`, and the page offers "Start fresh", which runs Delete now and
sends the sample again. No `GET` creates a sandbox or writes anything.

One address may start 10 sandboxes an hour. The 11th gets 429, "Too many new sandboxes
from your network. Try again later." An IPv6 address counts by its /64, and an
IPv4-mapped one as the IPv4 address. With `DOCKET_TRUST_PROXY=1` the address is the last
`X-Forwarded-For` hop, because the proxy appends it and anything before it came from the
client. Set it only behind such a proxy. The counters store an HMAC of the address under
`DOCKET_ADDRESS_KEY`, never the address. Changing the key starts each address's hourly
counts again; the day's sample count and spend carry on, since neither is kept per address.

**Routes.** Every route is in one table in `docket/review_access.py`, and the gate
refuses any route that is not:

- Public: `GET /`, `GET /home/{file}`, `GET /healthz`, `GET /reviewer` and
  `GET /assets/{file}`.
- Sandbox-scoped: the review queue, a document's status and file, review actions,
  samples, and `GET` and `DELETE /sandbox`. Each shows only the visitor's own sandbox. A
  document in another sandbox, or any document without a live sandbox cookie, gets the
  same 404, "Unknown document", as an id that does not exist.
- Closed: `POST /documents` and `POST /inbound` return 403 before any body is read, so
  visitors cannot send their own documents.
- Refused: FastAPI's `/docs`, `/docs/oauth2-redirect`, `/openapi.json` and `/redoc`
  return 404, like any route not in the table.

A Bearer token grants nothing in beta, whether or not one is configured. The review page
at `/reviewer` opens with no form.

**Request limits.** Every request that is not a `GET` must carry an `Origin` header that
matches `DOCKET_BASE_URL`, or it returns 403. An unset `DOCKET_BASE_URL` therefore
refuses every such request. Every body is limited to 16 KB. On a document's routes the
visitor's ownership of the document is checked before any of the body is read, so another
visitor's document, or one that does not exist, gets its 404 with nothing read.

**Review actions.** A dismiss reason must not be blank and has at most 300 characters.
An approve must name one of the candidates the review page offers for that document.
Both break the rule with 422.

**Sample documents.** A visitor cannot upload, so the beta offers samples instead: the
server makes one synthetic PDF of a case type the visitor picks and runs it through the
normal pipeline, in the visitor's sandbox. The model is called in the configured
`DOCKET_MODEL_MODE`, which is `live` on the hosted beta. In `replay`, a fresh sample has
no recording and ends `failed` with `replay_miss`. A document with no candidate record
never reaches the model in either mode. The sender shows as "Sample generator". Two
routes serve this, and both return 404 outside beta:

- `GET /beta/samples` lists the case types with plain labels, and a `state` of `ready`,
  `off`, `paused` or `catching_up`.
- `POST /beta/samples` takes `{"case_type": "<value from the list>"}` and returns the
  new `document_id`. The server picks the random seed, so a visitor never chooses a file.

Eleven case types are offered. Seven are left out: `corrupt`, `encrypted`, `zero_byte`
and `non_pdf` always end rejected; `empty_text_layer` is the same file for every seed;
`exact_duplicate` needs the document it copies; and `graded_evidence` has only about
2,000 possible files, so seeds repeat. The list and the reasons are in `docket/corpus.py`.

**Caps.** A send is refused when any of these holds:

- The sweep is behind: 503 with the message "Docket is catching up. Try again in a few
  minutes." (see the sweep below).
- Live answers are off: 503 with the message "Live answers are turned off right now."
- Live answers are paused: 503. Today's spend plus the most one document can cost is
  above `DOCKET_DAILY_BUDGET_USD`. The check runs again on every request, so the pause
  lasts until enough of today's budget is free. That is normally when the UTC day
  changes, but a call that costs less than it reserved frees some sooner.
- The send would start a sandbox, and the address has started 10 in the last hour: 429.
- The address has sent 20 samples in the last 60 minutes: 429 with a `Retry-After`
  header and a message naming the wait in whole minutes. The window rolls, so a slot
  frees 60 minutes after the send that took it.
- All 300 samples for the UTC day are sent: 429 with `Retry-After` set to the seconds
  until midnight UTC.

The new sandbox and the sample slots are taken together or not at all, so a refusal on
any cap takes nothing. A send that then fails at ingress keeps its slot; if it started a
sandbox, the browser never gets that cookie, so the sandbox is closed and the sweep deletes
whatever the failed write left in it. A send whose
sandbox ended, or was deleted before its write began, gets the 409 `sandbox_ended` answer
and writes nothing; a send in a sandbox's last five minutes gets `sandbox_ending`. Neither
takes a slot. The counts live in the
`sandbox_creations`, `sample_sends` and `sample_counts` tables, so they survive a
restart.

**Kill switch.** `docket beta-switch off` stops live model answers and
`docket beta-switch on` resumes them. It writes one row to the `beta_flags` table, and
it refuses to run unless `DOCKET_ENV=beta`. With no row, answers are on. Every process
reads the row before each live model request, so no restart is needed. A document whose
classify call is stopped ends `failed` with the reason `budget_exceeded`, the same
reason as an exhausted budget. A stopped choose call leaves the document without a
suggested record, and it is then filed or held as usual. A document already waiting for
review stays reviewable.
`docket spike-models` reads the switch too.

**Request bodies.** `POST /beta/samples` and `POST /review/{id}/{action}` read their
bodies as raw JSON text. A body that is not valid JSON of the right shape returns 422
with a fixed message that never repeats the input. Review actions do this in both modes.
Samples do it in beta; in local mode the route returns 404 before reading the body.

**Document trace.** `GET /documents/{id}` also returns `filename` and `sender`, so the
review page can show a document that is not in the queue, such as a sample being read.

**Delete now.** `DELETE /sandbox` deletes the visitor's sandbox from every store, then
answers. Every write to a sandbox holds the sandbox's lock shared for the whole write:
it checks that the sandbox is open, stores the PDF, writes the rows and starts the
workflow. Delete now takes the lock exclusively, waiting up to 10 seconds, then:

1. deletes the sandbox's row, which deletes its documents, events, sources and filings
   with it;
2. deletes each document's workflow history from Temporal;
3. deletes every object under `sbx/<sandbox>/`, waits 3 seconds, and asks SeaweedFS to
   vacuum, which rewrites its files without the deleted bytes. SeaweedFS frees a deleted
   object's bytes from a queue about a second after the delete, so a vacuum straight away
   would miss them.

It answers 200, `{"deleted": true}`, and clears the cookie. A write already under way
when Delete now arrives is deleted with the rest; one that reaches the lock after it
finds no sandbox and writes nothing. If the lock is not free within 10 seconds, Delete
now closes the sandbox instead, so no later write goes to it, and answers 202,
`{"deleted": false, "detail": "Deleting; this finishes within a few minutes."}`. A store
that fails gets the same 202. In both cases the sweep finishes the job. No cookie, or a
cookie for no sandbox, gets the same 200 as a real delete, so the answer reveals nothing.
Delete now works even while the sweep is behind.

A model call already in progress cannot be recalled: one waiting out a retry delay can
still send the document's text to the model for a few seconds after Delete now answers.

`GET /sandbox` returns `{"expires_in": <seconds>}` for the visitor's live sandbox, or
`{"expires_in": null}`. The review page counts down from it: "Private to this browser ·
deleted in N minutes · Delete now". Before its first write, the page checks that the
browser keeps cookies for the site. If it does not, the page says the sandbox needs a
cookie to stay private, and sends nothing. Sends and Delete now from all of a browser's
tabs take turns under one Web Lock, so two first sends cannot start two sandboxes and
Delete now's answer cannot clear the cookie of a sandbox a send just started; a browser
without the Web Locks API gets this only within one tab. Delete now is off while a send
waits for its answer. Delete now names no sandbox: from any tab it deletes the browser's
current one.

**The sweep.** `docket beta-sweep --service` is the `sweep` service in
`compose.prod.yaml`. It runs every 2 minutes, start to start, and waits for nothing but
the database schema. Each run has three passes, each against one store and each with
its own 50-second budget:

1. **Postgres.** Deletes every sandbox that is expired or closed. Those with documents go
   first, then closed ones, one at a time and under each sandbox's lock, oldest first. A
   sandbox whose lock is still held after 10 seconds is left for the next run. Expired
   empty ones go after, in bulk, oldest first. The pass also deletes counter rows more
   than an hour old, which no longer count toward any limit.
2. **Temporal.** Lists the `doc-` workflows on Docket's task queue and deletes each one
   whose document row is gone.
3. **S3.** Deletes the objects of every sandbox that is not live, then vacuums SeaweedFS,
   3 seconds after any delete. Every run vacuums, so bytes an earlier vacuum came too
   soon for go on the next run.

Each pass decides what to delete from what is live in Postgres, not from what another
pass did. So Temporal being down stops only the Temporal pass, and a pass cut short by
its budget carries on at the next run. A run in which all three passes finished records
the time in the `sweep_complete` row of `beta_flags`. A run that takes over 3 minutes
exits, and Compose restarts the service. `docket beta-sweep` runs once by hand and exits
1 unless all three passes finished. `docket beta-sweep --all` deletes every sandbox,
live ones too, every document outside a sandbox, and every object in the bucket. It is for
maintenance only, with the API, the worker and the `sweep` service stopped: it deletes rows
before objects, so a sample accepted while it runs could keep its rows and lose its file.

**The bound.** With a healthy sweep, everything a sandbox held is gone within 55
minutes of its start: a 50-minute life, up to 2 minutes until the next run, and up to 3
minutes for the run. If no run has completed in the last 10 minutes, the beta fails
closed: samples, and so new sandboxes, and review actions get 503 "Docket is catching up.
Try again in a few minutes.", and `GET /beta/samples` reports `catching_up`. The review page
asks again every 30 seconds and on "Refresh queue", and lets a sample go once it is ready. Data already in stays
until the sweep recovers. A held document's workflow ends on its own after
`DOCKET_REVIEW_TIMEOUT_SECONDS`, at most an hour in beta.

**What is wiped, and what stays on disk.** SeaweedFS's vacuum rewrites its volume files
without the deleted objects. Temporal runs with `--sqlite-pragma secure_delete=on`, so a
deleted history's bytes are zeroed in its file. Postgres keeps deleted rows in its files
until it reuses the space; the only free text among them is a dismiss reason, at most 300
characters, and the counters hold address hashes. `pg_dump` shows none of it. Docket
logs no document text, address or dismiss reason. The review page's preview is a blob
URL, released when the page closes. None of the beta's volumes is backed up, because a
copy would keep what a sandbox held past its hour.

**Status.** `docket beta-status` prints, from Postgres alone: the age of the last
complete sweep, sandboxes live and waiting for the sweep, documents, new sandboxes and
samples in the last hour with how many addresses are at each limit, the day's samples,
the switch and the day's spend. An office behind one address shares its limits, and this
is where that shows.

**Startup refusals.** The API, the worker, `docket beta-sweep`, `docket reconcile`,
`docket eval` and `docket spike-models` exit before doing any work when the
configuration is unsafe, and print every problem. They never print a secret value. They
refuse when:

- `DOCKET_ADDRESS_KEY` is shorter than 32 bytes;
- `DOCKET_REVIEW_TOKEN` is set to anything but blank;
- `DOCKET_TRUST_PROXY` is off;
- any variable starting with `DOCKET_FAILPOINT` is set;
- `DOCKET_DAILY_BUDGET_USD` is not set explicitly, or is above 1.00;
- the model mode is not `live` or `replay`. `docket eval` checks its `--mode`, and
  `docket spike-models` is checked as `live`, because each uses that mode;
- `DOCKET_REVIEW_TIMEOUT_SECONDS` is above 3600;
- `UVICORN_ROOT_PATH` is set.

**Site root only.** In beta, a request with a non-empty ASGI `root_path` gets 500, "The
beta must be served at the site root.", on every route, `/healthz` included. Under a root
path the router matches the path without it while the gate reads the full path, so a
private route could pass as a public one. Serve the beta at the root of its own host name.

**Health check.** In beta, `GET /healthz` returns `{"status": "ok"}`, or
`{"status": "stale"}` when no sweep has completed in the last 10 minutes. It is public,
so it says nothing more; `docket beta-status` has the rest. It reads the database, so it
fails when Postgres is down.

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

- **Provider.** `DOCKET_MODEL_PROVIDER` is `anthropic` (the default) or `openrouter`.
  The defaults, `anthropic` with `DOCKET_MODEL=claude-haiku-5-5`, replay the live
  answers recorded on 2026-11-13. Live calls need the key in `ANTHROPIC_API_KEY`.
  `openrouter` with `openai/gpt-4o-mini` replays the v0.1 simulated answers.
  OpenRouter calls use `OPENROUTER_API_KEY`. Keys come from the environment or the
  gitignored `.env`.
- **Saved answers.** Each provider has its own file, so live and simulated answers
  never share one: `evals/recordings/anthropic.jsonl`, or for OpenRouter the v0.1
  simulated answers in `evals/history/simulated/calls.jsonl`.
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
  A live document's run cap lives in the worker's memory, in that document's gateway.
  The worker keeps the gateways of the 10,000 most recent documents, and one HTTP client
  per provider and model. A document whose gateway was dropped, or any document after the
  worker restarts, starts again with a fresh run cap. The daily cap in Postgres is the
  hard bound on spend.
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
text. A beta test opens two browsers, each with its own sandbox, sends a sample and
dismisses it, and deletes the sandbox with Delete now; a second beta test blocks cookies
and checks that the page sends nothing. Their browser joins the beta API container's
network, because the `Secure` sandbox cookie reaches only an HTTPS site or `localhost`,
and Chromium treats `localhost` as secure only from there. The marker test runs there
too: it puts a marker in three sandboxes on the compose stack, deletes two, and checks
`pg_dump`, SeaweedFS's and Temporal's data folders and every container's logs. Nine more check token retention after access and asset failures. Chromium runs in a separate test image, not in the API or worker images.
`verify.sh` fails if any test is skipped, or if branch coverage of a core module
falls below its floor in `scripts/check_coverage.py`. Last, it reproduces the
held-out report and runs detect-secrets, which also scans the archives' contents.
The scan skips the local `.env`, which holds keys, and `verify.sh` fails if git
does not ignore `.env`.
CI runs these checks as parallel GitHub Actions jobs. Pull requests skip the fault suite
described below; every push to main, a nightly run at 07:00 UTC and a manual run include it.
A final job, `verify`, fails if any other job failed or was cancelled. It then checks the
coverage floors once, on the coverage merged from the test shards and the browser tests.

`./verify.sh --fault` also runs the fault suite in `tests/fault/`.
`docket/failpoints.py` stops a process at a named point when `DOCKET_FAILPOINT`
names it. The five tests crash the worker in the choose step and right after
storage, crash the API after it saves the raw file and after it creates the
document row, and stop storage while a restarted worker is running. Each test checks that the
document is never filed twice and still reaches a final status. One more stops Temporal
while the beta's sweep runs: rows and objects still go, and the histories go on the first
run after Temporal is back.

## Layout

| Path                      | What it holds                                         |
| ------------------------- | ----------------------------------------------------- |
| `docket/core/`            | Extraction, shortlist, scoring, storage and ledger    |
| `docket/llm/`             | Model gateway, replay store, exchange archive, budget |
| `docket/workflows.py`     | The per-document Temporal workflow                    |
| `docket/activities.py`    | The workflow's steps, run by `docket/worker.py`       |
| `docket/api.py`           | Upload, webhook, status and review endpoints          |
| `docket/webhook.py`       | Webhook signature check, size limit and rate limit    |
| `docket/review_access.py` | Reviewer access: token check, the beta route table, entry page |
| `docket/beta.py`          | Beta rules: startup refusals, client address, Origin check |
| `docket/sandbox.py`       | Beta sandboxes: the cookie, ownership and the write lock |
| `docket/sweep.py`         | Beta deletion: Delete now and the sweep               |
| `docket/evaluation.py`    | Evaluation runs and reports                           |
| `docket/cli.py`           | The `docket` command                                  |
| `docket/front_demo.py`    | Builds the front page's demo data from committed reports |
| `web/`                    | The review page and its Playwright tests              |
| `web/public/home/`        | The public front page, served as is                   |
| `config/`                 | Records, categories and scoring weights               |
| `migrations/`             | Alembic migrations for the Postgres tables            |
| `corpus/`                 | Committed synthetic documents and labels              |
| `evals/`                  | Reports, saved answers and the exchange archive       |
| `docs/adr/`               | Decision records                                      |
