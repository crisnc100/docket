# Docket "try your own document": plan (rev 1, 2026-11-21, draft for Cristian)

Built on main 01cefeb, which is what runs at https://docket.cristianortega.dev. The design
follows `advisor-brief.md` and the security advisor's verdict in `advisor-mythos.md`. Every
must-have and every cut from that verdict is adopted; where this plan goes further, it says why.

## Goal

People evaluating Docket can try it on their own documents with no sign-up and no invite
code. Each browser gets a private sandbox; nobody else can see or touch what is in it, and
everything in it is deleted within an hour, or at once with "Delete now". Uploads are safe
for the host: a hostile PDF cannot take down the worker, run up a bill, or turn Docket into
file hosting.

**Approved by Cristian on 2026-11-21** (not reopened here): three PRs; real documents
accepted with a one-line notice; no CAPTCHA until abuse happens; 5 uploads an hour and 15 a
day per address (an IPv6 address counted by its /64), 100 a day overall, the existing $1 a
day model budget; limits shown only when hit; no per-sandbox limit.

**Choices this plan adds, each with its reason:**

- **The server never receives the file name.** Names like "Lease - J Smith - 12 Elm St.pdf"
  are personal data, and today the name goes into the workflow input (`docket/ingress.py:127`)
  and the stored object's metadata. Uploads are named `upload.pdf` on the server; the page
  shows the real name from the browser's own copy. Cost: clues in the file name are not used
  for matching.
- **Uploads match only the visitor's own units (PR 3); samples match the sample building.**
  A real lease has nothing to do with the 200 synthetic units.
- **A 20-page cap.** Matching already sends any text over 64,000 characters (about 20 pages)
  to review (`docket/core/candidates.py:27`), so longer files gain nothing.
- **A sandbox lasts 50 minutes**, so that with a sweep every 2 minutes and each run capped at
  3 minutes, everything is gone by 55 minutes, inside the one-hour promise.
- **The uploads switch starts off.** With no row in `beta_flags`, uploads are refused; the
  operator turns them on after the deploy checks.
- **The hourly limit says "try again later"**, not "tomorrow", because tomorrow would be
  untrue. The two daily limits say "try again tomorrow". No message names a number.

## Checked while planning (2026-11-21)

- **Temporal keeps histories for a day.** The pinned dev server (`temporalio/temporal:1.5.1`,
  Server 1.29.1) gives the `default` namespace a 24-hour retention, so a terminated workflow's
  history, extracted text included, stays a day (`temporal operator namespace describe`).
- **Temporal can delete a history.** On that server, `temporal workflow delete` removed a
  running workflow, and `describe` answered "not found" within a second. A
  `WorkflowId STARTS_WITH 'doc-'` list query works too.
- **Deleted is not erased in Temporal's file unless asked.** After that delete, the workflow's
  input text was still readable in the SQLite file. Started with
  `--sqlite-pragma secure_delete=on`, the same test left no trace in the file.
- **An empty environment alone does not hide the worker's secrets.** In the app image, a
  child process with an empty environment, running as the same user (10001), read the parent's
  variables from `/proc/1/environ`. After the parent called `prctl(PR_SET_DUMPABLE, 0)`, the
  read failed with "Permission denied".
- **What Anthropic keeps.** See "Privacy copy" below.
- `docker-compose.yml` does not exist; the local stack is `compose.yaml`.

## Three PRs, one after another

**Why three:** each leaves main working and safe when merged alone. PR 1 replaces the invite
code and shared queue with private sandboxes, using the samples that exist today. PR 2 adds
uploads behind a switch that defaults to off. PR 3 adds the units form. Each needs the one
before it, so they are sequential: one worktree (`docket-sandbox`), branches `sandbox`, then
`sandbox-uploads` and `sandbox-units`, each cut from main after the previous one merges.

## Data model

- **`sandboxes`:** `id` (random UUID), `token_hash` (SHA-256 of the cookie value, unique),
  `created_at`, `expires_at` (created + 50 minutes), `closed_at` (set by "Delete now").
  The cookie value itself is never stored, so a copy of the database holds no live cookie.
- **The sandbox is part of a document's identity.** `documents` gets `sandbox_id`. Its unique
  key becomes (sandbox, mode, run_id, content hash), with an empty sandbox counted as equal
  (Postgres 16's `NULLS NOT DISTINCT`), so local mode keeps its rule that the same bytes give
  the same document (`docket/db.py:29`). `filed_documents` gets the same column and key
  (`docket/db.py:70`). Today two visitors sending the same PDF would share one document and
  one stored file.
- **Storage keys start with the sandbox:** the PDF at `sbx/<sandbox>/raw/<sha256>` instead of
  `raw/<sha256>` (`docket/ingress.py:66`). Local mode keeps `raw/`.
- **One delete removes every row.** The foreign keys from documents to sandboxes, and from
  events, sources and filings to documents, become `ON DELETE CASCADE`.
- **PR 2 adds** `upload_sends` (address, time) and `upload_counts` (UTC day, count), the same
  shape as the sample counters, and the `uploads` row in `beta_flags`.
- **PR 3 adds** `sandbox_units` (sandbox, server-assigned id, address, tenant names).

**How today's flows move onto sandboxes:**

- **Shared queue:** `GET /review` lists only the caller's sandbox's held documents.
- **Samples:** `POST /beta/samples` writes into the caller's sandbox. The ingress guard that
  checked the reset flag (`docket/beta_samples.py:83`) becomes a check, under a row lock, that
  the sandbox is open and not in its last 5 minutes.
- **Nightly reset:** removed with its starting queue. The `reset` service becomes `sweep`
  (below). `docket beta-reset` becomes `docket beta-sweep`, with `--all` to delete every
  sandbox at once.
- **Invite codes, the sign-in page, the lockout and `signin_failures`:** removed.
- **Workflows:** no workflow code changes in any PR, so ADR 002's patch rules are not
  triggered. Every change is inside activities, routes or the sweep.

## One ownership lookup

`owned_document(session, sandbox, document_id)` returns the document only when it belongs to
that sandbox, and otherwise raises the same 404 as an unknown id. In beta, a missing, expired
or closed sandbox matches nothing; it never falls back to documents without a sandbox. Every
route that takes a document id uses it, before reading the body or touching Temporal:
`GET /documents/{id}` (`docket/api.py:209`), `GET /documents/{id}/file` (`:236`) and
`POST /review/{id}/{action}` (`:303`). No route checks ownership today.

**The test:** it walks the app's route table. For every route with a document id it sends
cookie B, no cookie, an expired cookie and a random id, and requires the same status and body
each time; with cookie A the route works. A new route with a document id that the test does
not cover fails it. `GET /review` with cookie B lists none of A's documents, and no review
action for A's document reaches Temporal.

## Deletion, everywhere

| Where | What is there | How it is deleted |
| --- | --- | --- |
| Postgres | Sandbox, documents, events, sources, filings; units (PR 3) | One `DELETE` of the sandbox row; the rest cascades |
| S3 store | The PDF under `sbx/<sandbox>/` | Delete the prefix. Any `sbx/` prefix without a live sandbox is deleted too |
| Temporal | Each workflow's history: input, extracted text (`docket/activities.py:76`), dismiss reason (`docket/api.py:336`) | `DeleteWorkflowExecution` for each `doc-<id>`; then every `doc-` workflow whose document row is gone. Temporal runs with `secure_delete=on` |
| Filed copy | Approving writes a second copy today (`docket/activities.py:260`) | Never written for sandbox documents. The file step records the key it would use, under `sbx/<sandbox>/`, and marks the document filed |
| Logs | Nothing: no file name reaches the server, text is never logged, and the parser's error output is thrown away | Checked by the marker test |
| Browser | The blob URL of the visitor's own file | Revoked when the page closes (`web/src/main.ts:107`) |

**Temporal: delete the history, rather than keep text out of it.** Keeping text out would
leave the dismiss reason and the rest of the input in the history, and it changes what the
activities pass, which ADR 002 treats as a workflow change. Deleting the history covers all of
it, needs no workflow change, and works on the pinned server (checked above).

**When:**

- **Delete now:** closes the sandbox under its row lock, so no new document can be written,
  then deletes it as below before answering, and clears the cookie. If a step fails, the
  sandbox stays closed and the next sweep finishes it.
- **The sweep:** runs every 2 minutes, each run capped at 3 minutes, one at a time. It closes
  and deletes every sandbox past `expires_at`. Then it deletes orphans: Temporal workflows
  whose document is gone, `sbx/` objects whose sandbox is gone, and, in beta only, any
  document or object outside a sandbox. Last, it prunes counters past their windows.
- **Order:** Temporal first, then rows, then objects, so a running workflow stops before its
  rows go. A workflow or object that a race leaves behind is an orphan on the next run.
- **Bound:** 50-minute life + 2-minute interval + 3-minute run = 55 minutes at worst. A test
  pins the three constants against 60 minutes.
- **Backstops:** in beta, `DOCKET_REVIEW_TIMEOUT_SECONDS` drops from 86,400 to 3,000
  (`.env.beta.example:28`), and a startup refusal stops anything above 3,600.
  `GET /healthz` reports `last_sweep_age_seconds` and `oldest_sandbox_age_seconds`.

**What "deleted" means:** Docket removes the data from every store, so nothing can read it
back, and Docket's volumes are never backed up. Freed disk space is a different matter; see
decision 1.

## PR 1: private sandboxes

**Phases** (the builder commits at the end of each):

1. **Identity and ownership:** the migration, sandbox-aware ingress and storage keys, the
   ownership lookup on every document route, the scoped queue, and no filed copy for sandbox
   documents.
2. **The sandbox and the gate:**
   - `POST /sandbox` returns the open sandbox or makes one, and sets `__Host-docket_sandbox`
     (HttpOnly, Secure, SameSite=Strict, Path=/, Max-Age the seconds left; 128 random bits).
     The page calls it on load. The page's HTML never needs the cookie, because a Strict
     cookie is not sent when a visitor follows a link from another site.
   - Remove invite codes, the sign-in route and form, the lockout, and the session key.
   - The gate denies by default. In beta only the listed routes answer: `/`, `/home/*`,
     `/healthz`, `/reviewer`, `/assets/*` and the sandbox, sample, review and document routes.
     `/docs`, `/openapi.json` and anything new are refused until listed.
   - Kept as they are: the Origin check on every non-GET (`docket/review_access.py:185`), the
     16 KB body cap, the closed `POST /documents` and `POST /inbound`, the control-character
     and root-path refusals, and the samples' caps and kill switch.
   - Count an IPv6 address by its /64 in `client_address` (`docket/beta.py:134`). Samples get
     this too.
3. **Deletion:** "Delete now", the `sweep` service and `docket beta-sweep [--all]` (replacing
   `beta-reset`), Temporal history deletion, the orphan passes, `secure_delete=on` in
   `compose.prod.yaml` and `compose.yaml`, the health fields, the review-timeout refusal.
4. **Page and docs:** the review page opens with no form. A line says the sandbox is private
   to this browser and when it will be deleted, with a "Delete now" button. The
   shared-queue line (`web/src/main.ts:390`) goes. On the front page, "ask for an invite
   code" (`web/public/home/index.html:67`) becomes a link to try it, and the token paragraph
   (`:137`) is updated. Then the docs listed below, and ADR 010.

**Acceptance:**

| Check | Proved by |
| --- | --- |
| S1: the cookie has exactly its flags and a 128-bit value; only its hash is in the database | API test; `pg_dump` contains no cookie value |
| S2: ownership on every route (the two-cookie test above) | API test walking the route table |
| S3: in beta only the listed routes answer; a foreign `Origin` is refused on every non-GET | API test walking the route table |
| S4: two sandboxes sending identical bytes get two documents and two stored files; "Delete now" in one leaves the other readable | Test with a fixed-seed sample |
| S5: "Delete now" removes rows, objects and histories before it answers; the old cookie then matches nothing; a sample racing the delete leaves nothing | Test against the compose Temporal and S3 |
| S6: one sweep run deletes expired sandboxes and every kind of orphan; the 55-minute bound holds | Test with `expires_at` in the past; test on the constants |
| S7: marker test. A PDF carrying a unique string goes through a sandbox; after deletion the string is in no `pg_dump`, no S3 object, no Temporal list result, not in the Temporal SQLite file, and in no log. Before deletion it is found in the SQLite file, so the test can see it | Test using Ingress directly, plus `docker compose exec temporal` |
| S8: an auto-filed sandbox document writes no second object, and its file event names the would-be key under `sbx/` | Workflow test |
| S9: startup refusals no longer need invite codes; they refuse a review timeout above 3,600; the rest stay | Existing refusal tests, updated |
| S10: a new browser opens `/reviewer` with no form, sends a sample and reviews it; a second browser context does not see it; "Delete now" empties the page | Playwright test on the beta stack |
| S11: local mode is unchanged, including same bytes giving the same document | Existing local tests |
| S12: workflow histories still replay | `tests/workflow/test_workflow.py`, unchanged |

**Out of scope:** uploads; the units form; CAPTCHA; a cap on making sandboxes (rows are tiny,
gone within the hour, and the limits that matter are per address); a list of all your
documents (the queue still lists held ones, and a document you send opens by itself);
re-running the load measurement; alerts.

## PR 2: upload your own PDF

**Phases:**

1. **Measure first, before building.** Build the image (`docker build -t docket-measure .`,
   pypdf 7.0.0 from `uv.lock`). A generator in the tests makes the crafted PDFs (not
   committed): a compressed content stream that expands to gigabytes, a page of millions of
   text operators, a deep or looping page tree, and a file claiming thousands of pages. Run
   today's `extract_document` on each inside the image under the worker's limits
   (`docker run --memory 768m --cpus 1`), and record wall time, peak memory and outcome in
   `builds/sandbox/measure.md`. The advisor's figures (a 22 KB file: 22 s and 470 MB; a 73 KB
   file: over 1 GB at 45 s) came from pypdf 6.14.2 on the host. **The builder stops here and
   reports the numbers.** They set the child's limits below.
2. **The contained parser.** The `extract` activity (`docket/activities.py:68`) runs pypdf in
   a child process, in every mode, so the tests cover the one path the worker uses:
   - the child sets its own limits before reading any input: memory (start near 300 MB), CPU
     (near 10 s), no file writes, and a high OOM score so the kernel kills it first;
   - the parent kills it after a wall-clock limit (near 15 s);
   - it gets an empty environment and `python -I`; the worker marks itself non-dumpable at
     startup, so the child cannot read the worker's variables (see "Checked while planning");
   - it counts pages first and refuses more than 20 without extracting any text;
   - it stops extracting at 200,000 characters;
   - its error output is thrown away;
   - one parse runs at a time. The worker's 768 MB must hold the worker's own peak (276 MB in
     ADR 009) plus the child's cap. Waiting for a turn plus the parse must stay under the
     extract step's 30 s (`docket/workflows.py:35`): a short wait, then a retryable error.
   - A child that hits a limit ends the document `rejected` with the new reason `parse_limit`
     or `too_many_pages`, non-retryable, so it is parsed once and never retried (today it
     would be tried 5 times, `docket/workflows.py:68`).
   - Evaluation, the front-page demo and the CLI keep parsing the committed corpus in-process.
3. **The upload route.** `POST /sandbox/documents`, in this order:
   - Refuse at once if there is no open sandbox, the uploads switch is off, or the budget is
     paused.
   - Take the address slots (5 in the last hour and 15 in the last 24 hours per address, an
     IPv6 address by its /64, and 100 per UTC day overall) in one transaction, before reading
     any of the body.
   - Then read at most 3 MiB, check `%PDF-`, and hand it to ingress as `upload.pdf`. A body
     that fails after this keeps its slot, as samples do.
   - Suggestion: the page sends the file as the raw body (`Content-Type: application/pdf`).
     FastAPI reads a whole multipart form before the handler runs, so a form upload could not
     be refused before its body is read.
   - The gate passes this one path without its 16 KB buffering. In beta,
     `GET /documents/{id}/file` returns the unknown-id 404 for uploads. Samples keep their
     preview.
4. **Page, proof and docs.**
   - The upload button with the notice beside it, and a preview from the browser's own copy
     (a blob URL). After a reload: "Preview is only in the tab you uploaded from."
   - A PDF with no text layer says so, instead of "no matching unit", using the
     `characters: 0` the extract event already records (`docket/activities.py:80`).
   - The marker test through the real upload route, the docs, the Caddy block, and ADR 011
     with the measured numbers.

**Acceptance:**

| Check | Proved by |
| --- | --- |
| U1: each crafted PDF ends `rejected` (`parse_limit` or `too_many_pages`) within 20 s; the worker container does not restart; a normal sample sent next finishes | Test on the compose stack: upload, then check `docker inspect` RestartCount |
| U2: the child has an empty environment, cannot read the worker's `/proc/<pid>/environ`, and runs under the set limits | Test inside the app image (macOS does not enforce the memory limit, so this runs in Linux) |
| U3: a 21-page PDF is refused before any text is extracted; text past 200,000 characters is cut and the document is held `input_too_large` | Child tests |
| U4: a `parse_limit` document's history shows one extract attempt | Workflow test reading the history |
| U5: the 6th upload in an hour from an address gets "try again later" before any body is read; the 16th in 24 hours and the 101st of the day get "try again tomorrow"; two addresses in one /64 share a count, two /64s do not; the counts survive a restart | API tests, one with a body that never ends |
| U6: 3 MiB + 1 byte is refused; so is a body that is not a PDF; an oversize `Content-Length` is refused before reading | API tests |
| U7: with no switch row, or with it off, uploads get 503 before any body is read; on, they work; the page hides the button when off | API and browser tests |
| U8: no route returns uploaded bytes | Test walking the route table with a marker upload |
| U9: the server never sees the file name (`upload.pdf` in the input, events and S3 metadata); the page shows the real one | API and browser tests |
| U10: the marker test from S7, through the upload route and "Delete now" | Compose-stack test |
| U11: the notice is beside the button, with its link | Browser test |
| U12: Caddy lets 3.5 MB through on the upload path only | Deploy check (below) |

**Out of scope:** OCR; any other file type; a server-side preview of uploads; a separate
container for parsing (cut by the advisor); disk quotas (the counts times 3 MiB bound them);
CAPTCHA.

## PR 3: your own units

1. **Storage and matching:**
   - `PUT /sandbox/units` replaces the sandbox's list: 1 to 10 units, each an address and 1 to 3
     tenant names, each field 1 to 120 characters, with no control characters or NUL, as valid
     UTF-8 under the 16 KB body cap.
   - The server assigns the ids (`UNIT-9001` to `UNIT-9010`) and one building code. `Record`
     requires the `UNIT-####` form (`docket/config.py:28`), and keys stay safe.
   - For an upload, the shortlist step loads that sandbox's units inside the activity (today it
     loads the shipped list, `docket/activities.py:91`), so unit text never enters Temporal.
     The model is sent only ids (`docket/prompts.py:27`).
   - A document already read keeps its candidates if the list changes.
   - Writes close with the sandbox's other writes.
2. **The form:** a short form beside the upload button, rendered with `textContent` like the
   rest of the page.

**Acceptance:**

| Check | Proved by |
| --- | --- |
| R1: a generated lease naming a unit's address and tenant is filed as strong, or held with that unit as top candidate; the same lease with no units is held with "no matching unit" and a hint to add units | Workflow test on the compose stack |
| R2: 11 units, a 121-character field, NUL, a control character or bad UTF-8 is a fixed 422 | API tests |
| R3: sandbox B never sees A's units; units go with the sandbox | Two-cookie test; S6's sweep test extended |
| R4: unit text and ids appear nowhere in the Temporal history | Marker test on unit text |
| R5: script-like unit text shows as text | Playwright test |

**Out of scope:** pasting a free-form list; former tenants and aliases; more than 10 units.

## Limits and switches

| Limit or switch | Value | When hit |
| --- | --- | --- |
| Upload size | 3 MiB (Caddy lets 4 MB through on that path only) | Plain refusal; the page checks size first |
| Pages | 20 | Rejected `too_many_pages` |
| Extracted text | 200,000 characters | Cut; matching then holds it as `input_too_large` |
| Parser | Memory, CPU and wall-clock limits from the measurement | Rejected `parse_limit` |
| Uploads per address | 5 in the last hour, 15 in the last 24 hours (IPv6 by /64) | "Try again later" / "Try again tomorrow" |
| Uploads overall | 100 per UTC day | "Try again tomorrow" |
| Model budget | $1.00 a day, as today (`docket/beta.py:72`) | "Paused for today. Try again tomorrow." |
| Samples | 20 an hour per address, 300 a day, as today | Unchanged messages |
| `docket beta-switch on\|off` | Live model answers, as today | 503 |
| `docket beta-switch uploads on\|off` | New; a missing row means off | 503 before any body is read |
| Sandbox | 50-minute life, writes close 5 minutes before | "Delete it now to start a new one" |

## Privacy copy, verified

**The line beside the upload button:**

> Docket deletes your file within an hour. Its text may be sent to Anthropic's AI model,
> which doesn't train on it and normally deletes it within 30 days ([how Anthropic handles
> it](https://privacy.claude.com/en/articles/7996866)). Please use a non-sensitive or
> redacted document.

**The source for each part:**

- **"Deletes within an hour":** the sweep's 55-minute bound, and no backups (operator check
  below).
- **"May be sent":** only when a candidate unit is found (`docket/workflows.py:128`), and at
  most 8,000 characters of the text (`docket/core/extraction.py:44`).
- **"Doesn't train on it":** privacy.claude.com article 7996868 (dated August 18, 2026): "By
  default, we will not use your inputs or outputs from our commercial products to train our
  models." Its exceptions are feedback and an explicit opt-in. Docket sends no feedback, and
  the opt-in is an operator check.
- **"Normally deletes it within 30 days":** article 7996866 (dated July 1, 2026): "we
  automatically delete inputs and outputs on our backend within 30 days of receipt or
  generation". The exceptions are a separate agreement, usage-policy flags (kept up to 2
  years) and the law, which is why the line says "normally" rather than "not kept".
  - The platform docs page (`/docs/en/manage-claude/api-and-data-retention`) says
    conversation content "is not retained by default", except for Covered Models. The two
    pages disagree, so the copy uses the longer period.
  - Haiku 5.5 is not on that page's Covered Models list.
- **Keeping it true:** `docs/operations.md` records both sources and dates. Re-read them
  before any deploy that changes the line.

## Deploy on Atlas

Each deploy runs in the release folder on Atlas, with
`compose() { docker compose -f compose.prod.yaml --env-file .env.beta "$@"; }`. Check free
memory and disk first.

**PR 1:**

1. In `.env.beta`: delete `DOCKET_INVITE_CODES` and `DOCKET_SESSION_KEY`, and set
   `DOCKET_REVIEW_TIMEOUT_SECONDS=3000`. Then run
   `python3 scripts/check_prod_compose.py .env.beta`, which must print `OK`.
2. Run `compose up -d --build --wait --remove-orphans`. This runs the migration, replaces
   `reset` with `sweep`, and restarts Temporal with `secure_delete=on`.
3. Run `compose run --rm --no-deps sweep docket beta-sweep --all`. It deletes the old shared
   queue, its objects and its workflow histories.
4. **Caddy:** no change.
5. **Checks:**
   - `/healthz` shows `last_sweep_age_seconds` under 180;
   - `/reviewer` opens with no form;
   - two browsers get different sandboxes;
   - a sample gets a live answer;
   - "Delete now" empties the page;
   - `compose logs --tail 20 sweep` shows runs.

**PR 2:**

1. Back up the Caddyfile first:
   `sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%Y%m%d-%H%M%S)`.
2. Run `compose up -d --build --wait`. Uploads stay off, because there is no switch row.
3. Replace the Docket block with two `handle` blocks. Exactly one of them matches each
   request, so the 32 KB limit cannot stack on the upload path:

   ```
   docket.cristianortega.dev {
       handle /sandbox/documents {
           request_body {
               max_size 4MB
           }
           reverse_proxy 127.0.0.1:8001
       }
       handle {
           request_body {
               max_size 32KB
           }
           reverse_proxy 127.0.0.1:8001
       }
   }
   ```

   Then `sudo caddy validate --config /etc/caddy/Caddyfile`, which must end
   "Valid configuration", and `sudo systemctl reload caddy`. Askwell's block stays untouched.
   Never reload a file that did not validate. To roll back, copy the backup in, validate and
   reload.
4. **Caddy checks:**
   - a 40 KB POST to `/review` gets Caddy's 413;
   - a 3.5 MB POST to `/sandbox/documents` gets Docket's own refusal, which proves it passed
     Caddy;
   - an upload while the switch is off gets 503.
5. Run `compose exec -T api docket beta-switch uploads on`. Then:
   - upload one non-sensitive PDF;
   - upload one crafted PDF from the generator: it must end `parse_limit`, the worker's
     RestartCount must not change, and `docker stats` must stay under the worker's limit;
   - press "Delete now".
6. Leave uploads on or off per decision 2.

**PR 3:** run `compose up -d --build --wait`; Caddy does not change. Check that the form
saves, that a generated lease with matching units files or is held with candidates, and that
"Delete now" removes the units.

**Operator checks for Cristian.** These cannot be seen from the host:

- **Backups:**
  - no provider snapshot or backup of Atlas includes `docket-hosted_*` volumes. If the
    provider snapshots the whole disk, turn that off for this VM, or the one-hour promise is
    false;
  - no cron or systemd job copies them (none does today).
- **Anthropic:** the beta key's organization has not opted in to sharing data for training.
- **Caddy:** the Docket block has no `log` directive.

## Docs and README

- **PR 1:**
  - **README:**
    - the beta line at `:72`;
    - "Run the hosted beta": settings, the `sweep` service, the health output, and "reset by
      hand" becoming `beta-sweep --all`;
    - the "Back up" section (`:283`) is replaced by one line saying Docket's volumes are never
      backed up, because nothing in them needs restoring and a copy would break the one-hour
      promise.
  - **`docs/operations.md`:** "Hosted beta mode" rewritten for sandboxes, ownership, Delete
    now, the sweep and its bound, and what "deleted" means; "Nightly reset" removed.
  - **Other files:**
    - `.env.beta.example`;
    - `scripts/check_prod_compose.py` (no invite secrets; `sweep` in place of `reset`);
    - `DESIGN.md`'s sample-panel note;
    - ADR 010 (sandboxes and deletion, with the Temporal measurements above) and the ADR
      index.
- **PR 2:**
  - **README:** the Caddy block and the upload limits in "Limits".
  - **`docs/operations.md`:** uploads, the parser's limits, the switch, the privacy line and
    its sources.
  - ADR 011, with the parser measurements.
- **PR 3:**
  - **`docs/operations.md`:** the units form and how uploads are matched.

## Tests that change

PR 1 removes or rewrites tests for things it removes: invite codes, sign-in, the lockout, the
"four public routes" list, the nightly reset, and the shared queue. They are in
`tests/test_beta_gate.py`, `tests/test_beta_hosting.py`, `tests/test_beta_samples.py`,
`tests/test_beta_load.py`, `tests/workflow/test_beta_reset.py`,
`tests/workflow/test_beta_samples_workflow.py`, `tests/browser/test_beta_browser.py` and
`web/tests/beta.spec.ts`.

Per the standing rule, these changes:

- go in their own test-only commits;
- name each changed assertion and the behaviour that still guards it;
- are checked by another model for weakened assertions;
- are listed in the PR body.

## Checks while building

- **Builders:** run the tests for what they touch (`uv run pytest <paths>`,
  `npm --prefix web run build`), and stop containers afterwards.
- **At each PR's close:** the full `./verify.sh`; the fault suite runs on main.
- **UI phases:** an `/impeccable` critique and a `/qa-tester` browser pass before
  `/pr-review`.
- **Stop and come back:** builders stop at PR 2 phase 1, and before adding any new rule that
  this plan does not name.

## Decisions for Cristian

1. **Is removing the data from Docket's stores enough, or must the bytes be wiped from disk
   too?**
   - Today's check: a deleted Temporal input stayed in the SQLite file until `secure_delete`
     was on, which this plan adds.
   - Postgres also keeps deleted rows on disk until the space is reused. So does SeaweedFS
     until it compacts a volume (unverified when that happens under `weed mini`).
   - Wiping at disk level each sweep would mean `VACUUM FULL` on the touched tables, which
     locks them, and forced SeaweedFS compaction.
   - **Recommendation:** remove from every store, `secure_delete` for Temporal, never back up
     the volumes, and say exactly this in `docs/operations.md`. The visitor's line,
     "deletes your file within an hour", is then true in the sense people read it.
2. **Turn uploads on after PR 2, or wait for PR 3?**
   - With PR 2 alone, an upload has no units to match, so it always ends "no matching unit",
     and the model is never called (`docket/workflows.py:128`). The visitor learns only that
     the file was read.
   - **Recommendation:** deploy PR 2, run its checks with uploads on, then switch them off
     until PR 3 is deployed.
