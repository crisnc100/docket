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
