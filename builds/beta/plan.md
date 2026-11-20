# Docket hosted beta: plan (rev 4, 2026-11-19, approved by Cristian 2026-11-19)

This revises PR4 of `builds/v02/plan.md`, which Cristian approved on 2026-11-12. Since then:
- **Redesign (#7, #8):** `/` became a public front page, and the review page moved to `/reviewer`.
- **Real model (#5, #6):** merged.

Rev 3 moved the gate and split the work into three PRs. Rev 4 adds the fixes from the advisor's attack on rev 3 (`builds/beta/bounce.md`). Where rev 4 and rev 2 differ, rev 4 wins. Rev 2's checks D34 (the kill switch makes zero model calls) and D37 (no reviewer token in beta) are carried in below as A1 and B5.

## Goal

A shared demo of Docket on Atlas, at docket.cristianortega.dev, in the showcase's three layers:

- **Show and Try, public with no login:** the front page at `/` and its replay demo, as it is today. It costs nothing and writes nothing.
- **Live, behind an invite code:** the review page. A visitor sends synthetic sample documents through the real pipeline, gets real model answers, and reviews what gets held.
  - Live spend is capped at **$1.00 a day**, enforced in two places: Docket's own ledger, and a spend limit at the provider.
  - A kill switch stops live answers without a restart.
  - The demo resets every night.

Nothing changes what the model is for. The rules decide automatic filing. The model reads the category and suggests a record to the human reviewer.

**Clean room:** the same rule as v0.1. No names, code, data or business rules from any employer's tools.

**Out of scope:**
- Visitors uploading their own PDFs (Waiting on Cristian).
- A production multi-user setup.
- Spend or failure alerts.
- Any change to Askwell's site on Atlas.

## PRs: three, one after another

**Why each PR is safe to merge alone:** beta behaviour switches on only with `DOCKET_ENV=beta`, and nothing is deployed until all three merge. Local mode and its tests stay as they are, so `main` keeps working.

**Why they are sequential:** B needs A's gate, and C deploys what A and B build.

| PR | Branch | What |
|---|---|---|
| **A: Beta gate** | `beta` (this worktree) | `DOCKET_ENV`, the invite gate, closed write paths, the fail-closed cap, the startup refusals |
| **B: Sample documents** | `beta-samples` | The sample generator, the sample button and endpoint, the caps and the kill switch |
| **C: Run it** | `beta-run` | The nightly reset, `compose.prod.yaml`, `.env.beta.example`, the Dockerfile, the load run, the runbook and ADR-009 |

**Deploy:** after C merges, under Cristian's go-ahead of 2026-11-19 ("Yes, after each merge"). Merging A or B deploys nothing. The deploy steps are at the end.

## PR A: Beta gate

**Builds:**

1. **`DOCKET_ENV`:** `local` (the default) or `beta`.
2. **Invite gate, in beta only.**
   - **Codes:** come from `DOCKET_INVITE_CODES`, comma-separated. Blank entries are refused. Each code is at least 16 characters, and any code that appears in `.env.beta.example` is refused.
   - **Sign-in route:** `POST /reviewer/session` takes a code. A right code sets a cookie named `__Host-docket_session`:
     - flags: HttpOnly, Secure, SameSite=Strict, Path=/;
     - value: the issue time plus an HMAC over that time and the code's id, keyed by `DOCKET_SESSION_KEY` (at least 32 bytes). The code itself is never in it.
     - The server checks the age, and refuses a cookie older than 7 days.
     - A code removed from `DOCKET_INVITE_CODES` stops working on the next request.
   - **No second way in:** in beta, the Bearer reviewer token is refused on every route, and `DOCKET_REVIEW_TOKEN` must be unset. The cookie check runs before the current "token not configured" 503 (`docket/review_access.py:62-67`).
   - **Cross-site checks:** every gated request that is not a GET must carry an `Origin` that matches `DOCKET_BASE_URL`, or it is refused. This covers `/reviewer/session`, `/review/*` and `/beta/samples`.
   - **Wrong-code lockout:** ten wrong codes from one address within an hour lock that address out of new sign-ins for the rest of the hour. The count is kept in Postgres. A locked address keeps any cookie it already has.
   - **Which address:** with `DOCKET_TRUST_PROXY=1`, the address is the last `X-Forwarded-For` hop, the one Caddy adds. Anything a client puts before it is ignored. Beta refuses to start without `DOCKET_TRUST_PROXY=1`.
3. **Exactly four public routes:** `GET /`, `GET /home/*`, `GET /healthz` and `POST /reviewer/session`. A test pins this list against the full route table, so a new route is gated by default.
4. **Closed write paths in beta:**
   - `POST /documents` and `POST /inbound` are refused in the middleware with 403, before any body is read.
   - Bodies on every other route are capped at 16 KB, also before reading.
   - A dismiss reason is capped at 300 characters.
   - An approve `record_id` must be one of that document's candidates.
5. **Fail-closed spend:**
   - In beta, `DOCKET_DAILY_BUDGET_USD` must be set explicitly, and at most 1.00. The API, the worker and the reset command each refuse to start otherwise.
   - The Anthropic key is given only to the worker.
6. **Startup refusals, in the API, the worker and the reset command.** Each refuses to start in beta when:
   - the invite codes are missing or invalid;
   - `DOCKET_SESSION_KEY` is short;
   - `DOCKET_REVIEW_TOKEN` is set;
   - `DOCKET_TRUST_PROXY` is unset;
   - any `DOCKET_FAILPOINT*` variable is present (`docket/failpoints.py:5`);
   - the cap is missing or above $1.00;
   - the model mode is not `live` or `replay`.
7. **Sign-in page in beta:**
   - `/reviewer` shows the invite-code form, the token form restyled in #8.
   - The app's API calls use the cookie, and send no `Authorization` header (`web/src/main.ts:49-61`).
   - Local mode keeps the token flow.
   - The front page's nav already links `/reviewer` (`web/public/home/index.html:21`). Its "hosted beta not built yet" line (`:67`) becomes a static line inviting visitors to ask for a code. Nothing on `/` depends on `DOCKET_ENV`.
8. **Browser test in beta mode:** the stack's browser test signs in with a code and reviews one document. The builder picks how Chromium gets a secure context for the Secure cookie (for example, by reaching the API through localhost) and logs why.

**Acceptance (the Bar):**
- **A1:**
  - In beta, every route outside the four public ones answers 401 without a valid cookie. This is checked across the route table.
  - A valid Bearer token is refused too.
  - The API refuses to start with `DOCKET_REVIEW_TOKEN` set.
- **A2:**
  - A right code sets `__Host-docket_session` with exactly the flags above, and the value does not contain the code.
  - A cookie dated 8 days ago is refused.
  - A cookie for a removed code is refused.
  - A cookie signed with another key is refused.
- **A3:**
  - The 11th wrong code within an hour from one address is refused, even when the code is right.
  - Another address still signs in.
  - A client-supplied `X-Forwarded-For` prefix does not change the address used.
  - The count survives an API restart.
- **A4:**
  - A POST with a missing or foreign `Origin` is refused on each of the three POST routes.
- **A5:**
  - In beta, `POST /documents` and `POST /inbound` get 403 without the body being read (a test streams a body that never ends).
  - A 17 KB body is refused on gated routes.
  - A 301-character dismiss reason is a 422, and so is an approve for a record that is not a candidate.
  - Locally, all existing tests pass unchanged.
- **A6:** each refusal case in Build 6 stops the API, the worker and the reset command with a plain message.
- **A7:** the beta browser test passes, and the local browser tests pass unchanged.

## PR B: Sample documents

**PR A carry-in:** read the `/reviewer/session` body as raw bytes and validate it in the handler. Two hand-made malformed bodies still get 500 instead of 422 (fix check fix-1795149841455-f60646 x1). It fails closed.


1. **A one-document generator.** A new function makes one PDF of one case type from a seed.
   - `corpus.generate` and its random draws stay unchanged.
   - A test proves the committed seed-7 and seed-11 corpora still hash the same.
2. **Sample case types:** the generator's case types minus those that cannot work as fresh standalone samples:
   - **byte-identical across seeds:** encrypted, zero_byte, non_pdf, corrupt;
   - **ends rejected:** these too;
   - **needs a partner:** exact_duplicate.

   The list is derived in code from `CASE_TYPES` with a named exclusion set, and a test runs every listed type through two seeds:
   - **the hashes differ;**
   - **it ends filed or held.**
3. **`POST /beta/samples`** (gated):
   - It takes a case type, and the server picks a new seed.
   - The PDF goes through the normal ingress and workflow.
   - The visitor never sends text, a file or a seed.
   - In beta, held documents wait 24 hours (`DOCKET_REVIEW_TIMEOUT_SECONDS=86400`), not 1 hour.
4. **Caps:**
   - 20 samples an hour per address, and 300 a day overall, each enforced by one atomic conditional update in Postgres.
   - When the daily ledger has less than one document's price ceiling left, the button gets a 503: "Live answers are paused for today."
   - A document whose live call is refused mid-way ends as the code already handles it (`failed`, budget reason), and the page shows that reason in plain words.
5. **Kill switch:**
   - **Where it lives:** in Postgres, not in an environment variable, so it works without a restart. `docket beta-switch off|on` sets it.
   - **What it stops:** the sample endpoint returns 503 "Live model answers are turned off right now", and the gateway reads the switch before every live call and refuses when it is off.
   - **A document mid-workflow when it flips:** its next live call is refused and it ends as in Build 4.
   - Documents already in review stay reviewable.
6. **Shared queue:**
   - Visitors share one queue, and any invitee can settle any document.
   - The review page says so in one line.
   - This is accepted for a demo that resets nightly.
7. **Design:** after the build, the orchestrator runs an `/impeccable` critique of the sample button and its states, before `/pr-review`.

**Acceptance:**
- **B1:** for every listed case type, a sample runs through the normal workflow and ends filed or held. Its trace shows the same steps as an uploaded document, and the committed corpora hashes are unchanged.
- **B2:** a case type outside the list is a 422, and so is a body that carries a seed or text.
- **B3:**
  - With 50 parallel requests, no more than 20 succeed per address within an hour.
  - The day's 301st sample overall is refused.
  - Both counts survive a restart.
- **B4:**
  - With the ledger spent, the button gets the "paused for today" 503.
  - A document refused mid-way ends `failed`, with the budget reason shown on the page.
- **B5:**
  - Switching off makes the endpoint return 503 and makes zero model calls, counted at the transport, with no restart.
  - Switching back on restores both.
- **B6:**
  - In beta, no route lets a visitor's bytes reach ingress or storage. The only visitor text that is stored is the capped dismiss reason.

## PR C: Run it

**PR B carry-ins** (fix check fix-1795183332550-398a1b on review 2026-11-20-13-49-02-d61ac8). Fix these first:
- x1: Release the reservation when the switch read itself fails before any HTTP request.
- x2: Say samples use the configured model mode (live on the hosted beta) and that some case types never reach the model; consider pinning samples to live in beta.
- x3: Say a stopped classify call ends the document failed/budget_exceeded, and a stopped choose call leaves it without a suggestion.
- x4: Describe the pause as lasting until enough of today's budget is free, normally until the UTC day changes.
- x5: Re-render the pane when nothing is selected, as well as when the sample is open.
- x6: Say the fixed 422 applies in both modes on review actions, and in beta on sign-in and samples.
- x7: Correct the brief's mechanism text.
- x8: Correct the brief's mechanism text.

**Builds:**

1. **`docket beta-reset`.** It refuses to run unless `DOCKET_ENV=beta`. In order, it:
   1. sets a "resetting" flag, so samples get a 503 while it runs;
   2. terminates open workflows, and waits until they are closed;
   3. clears documents, sources, events and filings, and empties the bucket;
   4. prunes the per-address counters, keeping the daily spend ledger and the daily sample count;
   5. reseeds 12 seed-11 documents in replay mode, at zero model cost;
   6. writes the reset time, then clears the flag.

   A `reset` service runs it at 08:00 UTC. If the host was down at 08:00, the service runs a missed reset at startup. In beta, `/healthz` reports the age of the last reset.
2. **Bounded gateway cache.** The worker keeps one gateway per document forever (`docket/runtime.py:36`, `:52-54`). Bound it, so a long-running beta does not grow without limit.
3. **`compose.prod.yaml`:**
   - six services, each with rev 2's memory limit (3.3 GB in total), a CPU limit, `restart: unless-stopped`, a healthcheck, and log rotation;
   - only the API published, on `127.0.0.1`;
   - the Temporal UI not published;
   - the Anthropic key passed only to the worker;
   - Postgres and S3 passwords taken from `.env.beta`;
   - a SeaweedFS identities file built from the env, in place of `config/s3.local.json`.
4. **`.env.beta.example`:**
   - every beta setting by name, with placeholders that the startup refusals reject;
   - `DOCKET_DAILY_BUDGET_USD=1.00`, `DOCKET_TRUST_PROXY=1`, `DOCKET_REVIEW_TIMEOUT_SECONDS=86400`;
   - no `DOCKET_REVIEW_TOKEN`.
4b. **No sub-path (PR A carry-in, fix check fix-1795149841455-f60646 x2):** in beta, refuse any request whose ASGI `root_path` is not empty, and make it a startup refusal too. The gate and the router read paths differently under a root path.
5. **Dockerfile:**
   - `.dockerignore` excludes `.env*`, so add `!.env.beta.example`. Without it, the startup refusal that rejects codes copied from the example cannot read the example inside the image (found while building PR A).
   - the app stages run as a non-root user;
   - the browser test stage stays root;
   - base images are pinned by multi-arch digest, so they work on both Apple Silicon and Atlas.
6. **Load run:**
   - 60 samples in 10 minutes from several test addresses, plus review actions and one reset, while sampling `docker stats`;
   - the caps are raised for the run, through a load-only setting that beta refuses at startup;
   - model answers come from a local stub provider, so the run spends nothing and still exercises the live code path.

   The measured peak goes in the README and ADR-009.
7. **Runbook in the README:**
   - the Caddy site block, with a request-size limit;
   - `caddy validate` before every reload, and how to roll back to the backup;
   - how to start, check, switch off and on, reset, and back up the volumes.
8. **ADR-009, on hosting:** the Temporal dev server choice, the measured memory, and what a production setup would need instead, labelled "not measured".

**Acceptance:**
- **C1:**
  - The reset refuses outside beta.
  - In beta, a sample started during the reset gets a 503.
  - After the reset, the ledger and the daily count are intact.
  - 12 documents are waiting, and still waiting 2 hours later (with the 24-hour timeout).
  - The reset made zero live calls.
  - A missed 08:00 run is caught up at startup.
- **C2:**
  - The rendered compose, checked by a script that prints no values, publishes only the API port, on 127.0.0.1.
  - Every service has its memory and CPU limits and log rotation.
  - Only the worker gets the model key.
  - The stack comes up healthy locally from the example env, with real values filled in.
- **C3:** the load run stays under every limit, and the peak is published.
- **C4:** each runbook command was run once during the build, and its output matched what the runbook says.

## Deploy (after PR C merges)

1. **Before deploying, Cristian:**
   - creates a separate Anthropic API key for the Docket beta;
   - sets a monthly spend limit on it at the provider (about $31, which is $1 a day);
   - puts the key in a file on Atlas only (approved 2026-11-19). The deploy reads it from there, and it never passes through a transcript.
2. **Check Atlas:** free RAM and disk.
3. **DNS:** add a Vercel A record, docket → 2.25.132.209.
4. **Start the stack:**
   - copy the release to Atlas;
   - write `.env.beta` there, mode 600;
   - start the stack.
5. **Caddy:**
   - back up the Caddyfile and add the Docket block;
   - run `caddy validate`, then reload;
   - Askwell's block stays untouched.
6. **Check:**
   - `/healthz` answers over HTTPS;
   - `/` loads with no code;
   - an API call without a code is refused;
   - a code signs in at `/reviewer`;
   - one sample goes through with a live answer;
   - the kill switch turns live answers off and back on;
   - one reset runs by hand;
   - a browser check of the front page and the review page.

## Checks while building

- Builders run the tests for what they touch (`uv run pytest <paths>`, `npm --prefix web run build`), without Docker.
- The full suite, `./verify.sh --fault`, runs only at each PR's close.
- Each PR goes through `/pr-review`.
