# Hosted beta build log

## PR A: Beta gate

Choices made while building, and why.

- **Order of the beta checks.** The middleware checks the session cookie first, then the `Origin`, then the closed write paths, then the 16 KB body cap. A route outside the four public ones answers 401 without a cookie, as A1 requires. With a cookie, `POST /documents` and `POST /inbound` get 403. Every check runs before any body is read.
- **Counting sign-in attempts.** Each attempt is a row, written before the code is checked, under a lock for that address. A right code deletes its row. An address with ten rows in the last 60 minutes is refused, so no 60-minute window ever holds more than ten wrong codes, and parallel guesses cannot get past ten. Refused sign-ins get a `Retry-After` that runs until the oldest of those rows is an hour old. The time comes from the database's clock, so every API process uses the same window.
- **The cookie value.** It holds the issue time and an HMAC over that time and a SHA-256 of the code. It holds no code id. The server tries each configured code, so a removed code ends its sessions on the next request. The cookie has no `Max-Age`, so it carries exactly the four flags in the plan. The server refuses it after 7 days.
- **A blank `DOCKET_REVIEW_TOKEN` counts as unset.** A blank token grants nothing, and the local compose file passes one by default. Only a non-blank token stops a beta process.
- **`DOCKET_FAILPOINT*` counts as present even when empty.** The plan says "present". The fault tests pass `DOCKET_FAILPOINT=` with an empty value, so beta containers must not get that variable at all.
- **The cap must be set explicitly.** "Set" means pydantic saw a value from the environment, from `.env` or from code (`model_fields_set`). The $1.50 default never counts as set.
- **The placeholder check needs the example file.** It reads `.env.beta.example` beside the package. `.dockerignore` excludes `.env*`, so PR C must ship the file in the image (noted in the plan's PR C section).
- **`docket reconcile`, `docket eval` and `docket spike-models` refuse too.** Reconcile starts workflows, and eval and spike-models can spend on the model, so each one calls the same startup check as the API and the worker. `submit` and `status` only call the API, so they are left alone. The check uses the mode the command will actually run: `eval --mode`, and always live for `spike-models`.
- **Review requests that cannot succeed stop at the API.** Temporal keeps every update it receives, even one the workflow then rejects. So in both modes, an unknown action gets 409 and a dismiss reason with a NUL gets 422, with no Temporal call. In beta, a blank dismiss reason gets 422 as well. Locally the workflow still rejects a blank reason, as before.
- **The review rules apply only in beta.** The 300-character dismiss limit and the candidates-only approve are beta rules, so local mode keeps its existing rules and tests. The candidates come from the document's latest score event, the same list the review page shows.
- **The sign-in route is registered in both modes.** Locally it sits behind the reviewer token and answers 404, so the route table is the same in both modes.
- **Secure cookies in the browser test.** The beta browser container shares the beta API container's network (`docker run --network container:<api>`) and opens `http://localhost:8000`. Chromium treats localhost as a secure context and keeps `Secure` and `__Host-` cookies there. `DOCKET_BASE_URL` is `http://localhost:8000`, so the browser's `Origin` matches. A Chromium flag that marks an insecure origin as secure was the other option, but it is not clear that the flag covers cookies.
- **Two Playwright runs.** `DOCKET_ACCESS=invite` runs only `beta.spec.ts`, and any other value runs only the local specs. The local run still has its 11 tests.
- **How the held document gets in.** Visitors cannot upload in beta, so the browser test submits the document through `Ingress` from the test process, on the beta worker's queue.

## Filed for the PR

- **No startup refusal for `DOCKET_BASE_URL`.** If it is missing, no origin matches, so every POST is refused, sign-in included. The gate fails closed without a new rule.
- **Pruning the lockout rows (PR C).** The nightly reset should delete `signin_failures` rows that are more than an hour old. It should not reset a count. Each counted attempt is a row, and the window rolls.

## PR B: Sample documents

Choices made while building, and why.

- **The sign-in body is parsed in the handler.** FastAPI's own 422 echoes the input. A body that is not an object and holds a lone surrogate, or a number JSON cannot hold, made that echo unwritable, so the reply was a 500. `read_json` parses the raw bytes and gives one fixed message for every bad body. `/beta/samples` uses it too.
- **Sample case types.** The 18 case types minus seven. Four end rejected: corrupt, encrypted, zero_byte and non_pdf. `empty_text_layer` is the same file for every seed: the empty PDF draws only a rectangle, and ingress returns the earlier document for a repeated file. The plan's list missed it. `exact_duplicate` needs the document it copies. `graded_evidence` has no seeded filler text, so its file depends only on the record and the profile: about 2,000 possible files, which seeds soon repeat, and a repeat takes a cap slot for an old document. That leaves 11. The reasons sit beside each name in `NOT_SAMPLES`.
- **The generator.** `sample_document` repeats each listed case type's content rules from `generate`, drawing from its own seeded generator, so `generate` and its draws are untouched.
- **Byte-for-byte corpus check.** 171 of the 181 committed files per seed match a fresh run. The 10 encrypted files never match, even on `main`, because pypdf encrypts with random bytes. `test_corpus.py` already compares their decrypted text.
- **Cap windows.** The per-address cap is a rolling 60 minutes: one `sample_sends` row per sample, counted under an advisory lock for that address, as the sign-in lockout counts wrong codes. A clock-hour bucket would let an address send 20 just before the hour and 20 just after. The day cap is one row per UTC day in `sample_counts`, raised by a single `INSERT ... ON CONFLICT DO UPDATE ... WHERE count < 300`. Both run in one transaction, so a refusal on either takes neither. Times come from the database's clock.
- **Order of the sample checks.** The kill switch, then the daily headroom, then the caps, then the generator, then ingress. A slot used by a request whose ingress then fails stays used, so the caps fail closed.
- **One document's price ceiling.** The gateway's own `worst_case` for the classify and choose calls, for the longest text the model is given (8,000 characters, at 4 UTF-8 bytes each), with every record as a candidate. About $0.015 at Haiku prices. Samples pause when today's ledger has less than that left.
- **The kill switch ends a document the budget way.** When it is off, the gateway raises the existing `BudgetExceeded` before every live attempt. A document mid-workflow ends `failed` with `budget_exceeded`, so the workflow needs no change. The page wording covers both causes. As before, a refusal in `choose` falls back to no suggestion, and the document still ends held or filed.
- **The switch is read only in beta.** The runtime and the spike command pass the switch reader to their gateways only when `DOCKET_ENV=beta`, so local live runs are unchanged. A missing row means on. `docket beta-switch` refuses outside beta.
- **Review bodies are parsed in the handler too, in both modes.** `POST /review/{id}/{action}` had the same 500 for a body that was not an object and held a lone surrogate, or a number JSON cannot hold. No existing test asserts the local 422's body, so the fixed message applies locally as well.
- **A gated `GET /beta/samples`.** It lists the case types with plain labels and says whether samples are ready, off or paused. The page builds its picker from it. Both sample routes are registered in both modes and answer 404 locally, like the sign-in route.
- **Samples arrive as `source="sample"`** from "Sample generator", with the server's file name. Their events match an upload of the same bytes (`tests/workflow/test_beta_samples_workflow.py`).
- **Workflow tests without S3.** The beta workflow tests swap in an in-memory store, so they need only Postgres and Temporal's test server.
- **No code default change for the review timeout.** The 24-hour wait comes from `DOCKET_REVIEW_TIMEOUT_SECONDS` in PR C's env file. Ingress already reads it into each workflow.

## Filed for PR B

- **No plain words for a refused suggestion.** When the budget or the switch refuses the `choose` call, the document is still held, with `suggestion_failure_reason: budget_exceeded` in its trace. The page shows only "No record suggested".
- **Pruning the per-address rows (PR C).** The nightly reset should delete `sample_sends` rows more than an hour old, and keep `sample_counts`.

## PR C: Run it

Choices made while building, and why.

- **x1, a failed switch read.** The transport reads the kill switch after the gateway has reserved the call's worst case. If that read raises, the transport raises `NotSent`, because nothing went out. The gateway then gives the reservation back and retries, and its own read, before any reservation, raises the database error to the activity.
- **x2, samples are not pinned to live.** They use the configured `DOCKET_MODEL_MODE`, which `.env.beta.example` sets to `live`. One setting decides how the model is called. The kill switch is the way to stop live answers without a restart.
- **The reset's flag and time live in `beta_flags`.** The `resetting` row is true while a reset runs. Once it is cleared, its `updated_at` is the finish time, which `/healthz` and the catch-up rule read. No migration was needed.
- **A failed reset leaves the flag set.** Samples keep getting 503 until a reset finishes, and the service tries again in five minutes. A reset cut off by a crash counts as due at the next start.
- **Which workflows the reset closes.** It closes those of documents not in a final status, from the documents table, as reconcile does. Temporal's test server cannot list workflows, and C1 runs there.
- **The starting queue.** It holds twelve seed-11 documents that the recorded live answers hold for review: one or two of each hold reason, the injection case and the refused `very_long` case. The reset test checks that every one replays with no `replay_miss`, and that all twelve are still waiting two hours later, with the 24-hour timeout.
- **The reset command runs in its own container.** `compose exec reset docket beta-reset` put a second Python process in the `reset` service's 256 MB limit and peaked at 255 MB. `compose run --rm --no-deps reset docket beta-reset` runs it in a container of its own.
- **Bounded gateways.** The worker keeps the 256 most recent gateways and one transport per provider and model, so dropping a gateway closes no HTTP client. The transport's output mode was already shared across the process.
- **Site root only.** In beta, a request with a non-empty `root_path` gets 500 on every route, and `UVICORN_ROOT_PATH` is a startup refusal. A uvicorn `--root-path` flag cannot be seen at startup, so the per-request check covers it.
- **Compose settings.** `compose.prod.yaml` takes every value by interpolation, and each secret is required (`${NAME:?...}`). So every command needs `--env-file .env.beta`, and a missing value stops compose with its name. Compose prefers a shell variable over the env file, so the check script refuses to run when one of the settings is set in the shell.
- **The project is named `docket-hosted`.** A worktree named `docket-beta` would otherwise share its containers and volumes with the dev stack.
- **The SeaweedFS identities file** is written by the container's own shell from its environment at start. The keys must be letters and digits, because they go into the JSON as they are.
- **The load run's caps are the beta's own.** 60 samples from four addresses is 15 each, under 20 an hour and 300 a day, so no load-only setting exists. The stub provider is reached through the Anthropic SDK's `ANTHROPIC_BASE_URL`, which only `scripts/load/compose.load.yaml` sets. The compose check fails if `compose.prod.yaml` ever sets it.
- **Caddy's body limit is 32 KB.** It sits above Docket's 16 KB, so Docket's own 413 is the refusal a visitor sees.
