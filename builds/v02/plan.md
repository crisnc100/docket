# Docket v0.2 — plan (rev 2, 2026-11-12, approved by Cristian)

## Goal

Three pieces of work, approved by Cristian on 2026-11-12:

1. **Real model.** Replace the simulated model answers with a real model.
   Docket's two model calls (category and record suggestion) go to Claude Haiku 5.5
   (`claude-haiku-5-5`) on the Anthropic API. One live pass over both test seeds is
   recorded, so CI replays it with no key. The live figures replace the simulated ones.
   The simulated answers stay in the repo as labelled history.
2. **Value calculator.** A page that puts the measured model cost per document next to a
   value the reader sets: documents a month, minutes saved and staff cost.
3. **Hosted beta.** A shared, invite-only demo on Atlas, using synthetic documents only.
   It has a daily spend cap, a kill switch and a nightly reset. The plan builds everything
   needed. The deploy itself waits for Cristian's go-ahead.

Nothing changes what the model is for. The rules decide automatic filing. The model only
suggests a record to a human reviewer and reads the category.

**Clean room:** the same rule as v0.1. No names, code, data or business rules from any
employer's tools.

## PRs: four

Four PRs, one after another, because each one stands alone and leaves `main` working:

| PR | What | Needs a key or money? | Starts after |
|---|---|---|---|
| **PR1** | Anthropic transport, live recording format, price snapshot, predictions | No | now (branch `real-model`) |
| **PR2** | The spike and the one live pass; reports, README and ADRs move to live figures | Yes, at most $5 | PR1 merged |
| **PR3** | Value calculator page | No | PR2 merged |
| **PR4** | Hosted beta build: invite gate, sample button, caps, reset, `compose.prod.yaml` | No (deploy is a separate go-ahead) | PR1 merged |

**Why PR1 and PR2 are split:** the cost controls get reviewed before any money is spent.
PR1 is code with fake HTTP tests. PR2 is mostly generated data and docs.

**Order:**
- PR1 → PR2 → PR3 is sequential. PR2 needs PR1's transport. PR3 needs PR2's measured cost.
- PR4 may run in parallel with PR2 and PR3 once PR1 has merged. It gets its own worktree
  (`wnew`). Before calling it independent, `git merge-tree --write-tree` must show no
  conflict with the PR in flight. Both touch `docket/api.py` and `web/`, so a conflict is
  likely. When the other PR merges, merge `main` into PR4 and re-close it.
- The deploy step (end of PR4) waits for PR3 to merge, so the beta shows the calculator,
  and for Cristian's go-ahead.

**If the live pass misses a gate, the work stops.** Haiku is the only candidate, by
Cristian's choice. A miss does not move up a model tier. The run's evidence is committed on
the PR2 branch, the orchestrator reports to Cristian, and PR3 and PR4's deploy wait for his
call.

## Stack changes

- **New dependency (approved):** the official `anthropic` Python SDK (`anthropic>=2.8.0`,
  as in Ledgerline), used through `AsyncAnthropic`, because the gateway is async. Its own retries are off; the
  transport retries.
- The OpenRouter transport and `langchain-openai` stay. A setting picks the provider.
- No other new dependency. The beta uses the same images.

## Definitions

- **Request:** one model call, keyed as in v0.1 by activity, prompt version, model id and
  the normalized input. Changing the model id to `claude-haiku-5-5` gives new keys, so live
  and simulated answers can never mix.
- **Live pass:** one run of `docket eval --mode record` over seed 7 and seed 11. That is
  440 unique requests (220 per seed: 110 category, 110 suggestion). Each request is asked
  once.
  - A request that got an answer is never asked again, even if the answer was unusable.
  - A request that got **no answer** (the connection failed before sending, or rate limits
    outlasted the retries) may be sent again in the same pass. It was never billed.
  - Prompts, schemas and settings are frozen before the pass. Nothing is tuned on seed 11.
- **Unreadable answer:** the API answered, but Docket cannot use it. Four reasons:
  - `refusal`: the API's safety filter declined (`stop_reason: refusal`).
  - `cut`: the answer stopped at the token limit.
  - `not_json`: the text is not one JSON object.
  - `schema`: the JSON fails the per-call schema, for example a record id not on the
    shortlist.

  An unreadable answer is charged, recorded with its reason and raw text, and raises the
  same typed error in replay as it did live. A failed category call still fails the
  document. A failed suggestion does not (v0.1 PR4).
- **No answer:** a transport failure after retries. Counted apart from unreadable answers.
- **Request shape:** system prompt as `system`, document text in the one user turn,
  `output_config.format` with the JSON schema, thinking off, effort `low`, no temperature.
  Haiku 5.5 allows thinking off at effort `high` or below. Thinking tokens bill as output,
  and choosing from a short list needs none. Schema `$ref`s are inlined and every object
  has `additionalProperties: false`, because the API's structured output requires it.
- **Schema fallback:** if the API refuses the schema itself (a 400 naming the schema or its
  grammar), the transport switches, for the rest of the process, to asking for JSON in the
  prompt. The gateway validates either way. Ledgerline's large schema was refused this way;
  Firstcall's small one was accepted. Docket's schemas are smaller than Firstcall's: an
  enum of five categories, and an enum of up to five record ids plus `none`. The output mode
  is recorded on every answer.
- **Retries:** rate limits (429), overload (529), server errors and connections refused
  before sending are retried up to 4 times with backoff. A timeout or a dropped connection
  after sending is charged at its worst case and not retried, because it may have been
  billed.
- **Price snapshot:** `config/model_prices.yaml`, with its date and source. Haiku 5.5:
  $0.10 per million input tokens and $0.50 per million output tokens, for prompts up to
  100,000 tokens ($0.50 and $2.50 above). Docket's longest request is far below that.
- **Cost of a call:** input tokens × input price + output tokens × output price, from the
  usage the API returns. The API returns tokens, not dollars.
- **Cost per document:** total model cost of the live pass divided by documents.
  It is reported three ways:
  - per document that reached the model (two calls);
  - per document that was not rejected;
  - per document overall, including rejected documents and empty shortlists, which cost
    nothing.
- **Spend limits for the live pass:**
  - **Hard cap $5:** the per-run budget. The gateway reserves each call's worst case before
    sending, as in v0.1.
  - **Callback at $2:** the pass runs with a $2.00 run budget. If it stops at that budget,
    the orchestrator calls back to Cristian before going on, up to $5.
  - **Daily ledger:** set to $5 for the run.
- **The key:** the same Anthropic key Firstcall uses, so spend is not traced per project
  in the console. The $5 cap and the daily ledger are the limits. Docket reads it from
  `ANTHROPIC_API_KEY` in the gitignored `.env` at the repository root (pydantic-settings,
  as for every other setting). In the beta it goes in `.env.beta` on Atlas. It is sent only as a request
  header. It never appears in logs, error messages, reports, recordings or object reprs.
- **Live archive:** `evals/recordings/anthropic.jsonl`. Each entry holds the request key,
  the outcome (`answer` or `unreadable` plus reason), the parsed output, the raw text,
  input and output tokens, cost, latency, output mode and provenance:
  `{provider: anthropic, model: claude-haiku-5-5, recorded: <date>}`.
  The request text goes in a packed exchange file, as in v0.1.
- **Simulated history:** the v0.1 simulated answers, their exchange archive and the
  2026-11-09 to 2026-11-11 reports move to `evals/history/simulated/`. They are unchanged,
  keep their banners, and get a short README saying what they were. `pack_exchange.py
  --check` keeps checking them.
- **Beta:** `DOCKET_ENV=beta`. A shared demo company, reached at a cristianortega.dev
  subdomain behind Caddy on Atlas. Synthetic documents only.

## Measures

The live pass reports everything the v0.1 eval reports, now from real answers:

- filing precision, recall, auto-file rate and review rate, against the rules-only baseline;
- wrong filings and filings to injected record ids;
- the eight-point threshold curve and the per-case-type table;
- pipeline category accuracy, and category accuracy on documents that reached the model;
- the suggestion against the rules' top candidate on review documents;
- Wilson 95% intervals on every proportion.

New in v0.2:

- **unreadable answers,** by reason and by activity, and **no-answer** count;
- **output mode** per answer (schema or JSON in prompt);
- **spend:** total, per activity, and the three costs per document;
- **latency per call:** median and 95th percentile. Reported, not gated.

## Gates

The live pass passes only if all of these hold:

- **G1** Unreadable answers ≤ 2% of the 440 requests (at most 8).
- **G2** No-answer requests ≤ 2% after the one allowed resend.
- **G3** The four held-out floors from v0.1 on seed 11: zero wrong filings, at least 90% of
  clean documents filed, zero filings to an injected id, and recall at least the
  rules-only baseline's.
- **G4** Spend stays under the $5 hard cap.

Suggestion accuracy and category accuracy are reported whichever way they fall. They are not
gates, because the model only suggests.

## Predictions (committed in PR1, before any live call)

These go in `docs/adr/008-real-model.md` with an empty Result section. PR2 fills in the
result next to each one, right or wrong.

| # | Prediction | Basis |
|---|---|---|
| P1 | The API accepts both schemas as structured output. No fallback is needed. | Docket's schemas are smaller than Firstcall's, which was accepted. |
| P2 | Unreadable answers ≤ 4 of 440 (1%). Refusals ≤ 2. | Enum outputs leave little room to go wrong. **Risk:** in the v0.1 simulation, Haiku running inside a coding agent blocked 19 of 444 synthetic requests (4.3%). The direct API has a different setup, but this could miss G1. |
| P3 | The whole live pass costs ≤ $0.15. | 440 calls; a mean request is about 2,700 characters, so well under 1,500 input tokens with the schema; outputs are a few tokens. |
| P4 | Mean cost per document that reaches the model ≤ $0.0004. | Two calls per document, from P3. |
| P5 | Category correct on ≥ 115 of the 120 seed-11 documents that reach the model. | The simulation got 120/120. |
| P6 | The suggestion is correct on 38 to 50 of the 102 seed-11 review documents. The rules stay at exactly 62/102. | Simulated Haiku got 44. The rules are deterministic. |
| P7 | Filing decisions match the rules-only baseline on all 180 seed-11 documents, unless a category failure fails a document. All four floors pass. | The model does not decide filing. |
| P8 | Median latency per call ≤ 1.5 s, 95th percentile ≤ 4 s. | Short inputs, thinking off. |
| P9 | The beta stack uses ≤ 1.5 GB of RAM in steady state and ≤ 2 GB at peak. | Temporal's dev server, Postgres, SeaweedFS, the API and one worker, each small. Unmeasured until PR4. |

## PR1 — Anthropic transport (no key, no spend)

**Builds:**
1. `docket/llm/anthropic.py`: the transport, as defined above. It returns the text,
   stop reason, tokens and output mode. The gateway charges the call before parsing.
   Learn from Firstcall's `firstcall/model/anthropic.py` (schema fallback, stop reasons)
   and Ledgerline's `ledgerline/llm/anthropic_transport.py` (SDK use, retry rules). Do not
   copy either.
2. A provider setting: `DOCKET_MODEL_PROVIDER=anthropic|openrouter`, defaulting to
   `anthropic`, with `DOCKET_MODEL=claude-haiku-5-5`. `runtime.py` builds the matching
   transport. `.env.example` gains `ANTHROPIC_API_KEY=` (empty).
3. `config/model_prices.yaml` and a loader. `PriceCeiling` comes from it, so the worst-case
   reservation uses the model's real prices.
4. The live recording format and outcomes. Unreadable answers are recorded and replay as
   the same error. Record mode still refuses anything but the synthetic corpus.
5. Report labels: a live report says at the top "Model answers measured live from
   claude-haiku-5-5 on the Anthropic API on <date>." A report can never mix live and
   simulated answers; the eval refuses.
6. `docket spike-models` works with the Anthropic transport (two calls, $0.05 budget).
7. `docs/adr/008-real-model.md` with context, the decision, predictions P1–P8 and an empty
   Result section. Committed before PR1's review.

**Tests:** fake HTTP only. No live call runs in PR1 or in CI.

**Acceptance:** D18–D23.

## PR2 — The live pass (needs the key; at most $5)

**Steps, in order:**
1. Check that ADR-008's predictions are on `main` (PR1 merged).
2. Run the spike: two live calls. Record whether the schema was accepted (P1).
3. Run the live pass over seed 7, then seed 11, with a $2.00 run budget. Callback to
   Cristian if the budget stops it.
4. Resend no-answer requests once, if any.
5. Check G1–G4. **On a miss, stop.** Commit the evidence to the branch and report.
6. On a pass:
   - Pack the live exchange archive.
   - Write both seed reports and the shadow diff.
   - Write the cost summary `evals/cost/<date>-claude-haiku-5-5.json`, built by a script
     from the recordings and the price snapshot.
7. Move the simulated archive and reports to `evals/history/simulated/`. Point
   `scripts/reproduce_eval.py` at the live archive.
8. Update docs with live figures:
   - the README's results table and limits;
   - ADR-004, now history: it points to ADR-008;
   - ADR-008's Result section, each prediction marked right or wrong;
   - `docs/rerecord.md` for the Anthropic path;
   - `evals/recordings/README.md`;
   - `docs/failures/` entries for any unreadable answer, and any new wrong suggestion
     pattern.

**Acceptance:** D24–D28.

## PR3 — Value calculator

**The page** (`web/`, Vite + TypeScript, served by FastAPI):
- **Cost side:** read from the committed cost summary. It shows:
  - the model id, the run date and the price snapshot date;
  - cost per document, by the three definitions;
  - a slider for documents a month, giving model cost a month;
  - a slider for hosting cost a month, default $0, labelled "your hosting".
- **Value side:** sliders the reader sets:
  - documents a month;
  - share filed automatically. The default is the live seed-11 rate, labelled "synthetic
    test set, not your mail";
  - minutes to file one document by hand;
  - minutes saved on each reviewed document, because candidates arrive ranked;
  - staff cost an hour.
- **Formula, shown on the page:**
  - Hours saved a month = documents × (auto share × minutes by hand + review share ×
    minutes saved in review) ÷ 60.
  - Value a month = hours saved × staff cost an hour.
  - Net = value − model cost − hosting.
- **Labels:** the defaults are labelled as examples to replace. A line under the result says
  the rules file documents at no model cost. The model's spend buys category reading and
  suggestions only.
- **Access:** the page and its JSON hold no document data. They are open without the
  reviewer token locally (decision 5). In the beta, the invite gate covers them like
  everything else.

**Design with /impeccable, run by the orchestrator:**
1. **Before the builder brief:** the orchestrator runs `/impeccable` to shape the page.
   The output is the direction, layout and a comp, saved to `builds/v02/calculator-design.md`.
   The builder brief points at that file.
2. **After the build, before `/pr-review`:** the orchestrator runs `/impeccable` again to
   critique and polish the built page against that file. Confirmed fixes go back to the
   builder.

**Acceptance:** D29–D31.

## PR4 — Hosted beta (build; deploy needs go-ahead)

**Sizing.** Temporal makes the stack heavier than Ledgerline's single container. The beta
runs six services:

| Service | Memory limit | Why |
|---|---|---|
| Temporal dev server (SQLite file on a volume) | 768 MB | One process with no cluster. Fine for a demo that resets nightly; not a production setup. |
| Postgres 16 | 512 MB | Documents, events, filings, spend ledger. |
| SeaweedFS (S3) | 512 MB | Raw and filed PDFs. A day of samples is a few MB. |
| api | 512 MB | |
| worker | 768 MB | Matching and PDF parsing. |
| reset | 256 MB | Sleeps until the nightly reset. |
| **Total ceiling** | **3.3 GB** | Atlas has 15 GB and already runs Ledgerline (1 GB limit). |

PR4 measures real use locally (P9) with a load script: 60 samples in 10 minutes, review
actions and one reset, while `docker stats` is sampled. The measured peak goes in the
README and ADR-009. Atlas had about 14 GB available on 2026-11-12 (`free -m`, with the
Ledgerline beta running). The deploy step checks it again.

**Builds:**
1. **Invite gate** on every page and API route except `/healthz`.
   - The codes come from `DOCKET_INVITE_CODES`.
   - A right code sets an HttpOnly, Secure, SameSite=Strict cookie holding a hash of it.
   - Ten wrong codes from one address within an hour block new logins from it for the hour.
   - In beta, the gate replaces the reviewer token. The token flow stays for local use.
2. **Synthetic documents only.** In beta, `POST /documents` and `POST /inbound` are refused.
   A "Send a sample document" button asks the server to generate a fresh synthetic PDF
   with the corpus generator (a new seed, a case type the visitor picks) and submits it
   through the normal ingress and workflow. Visitors cannot send their own text.
3. **Caps:**
   - samples per address: 20 an hour;
   - samples for everyone: 300 a day;
   - live model spend: the existing daily ledger, `DOCKET_DAILY_BUDGET_USD`, default $1.00.

   When a cap is hit, the button gets a 503 with a plain message.
4. **Kill switch.** `DOCKET_LIVE_MODEL_OFF=1`: the sample button returns 503 "Live model
   answers are turned off right now", and the gateway refuses any live call. Documents
   already in review stay reviewable.
5. **Nightly reset** (`docket beta-reset`, run by the `reset` service at 08:00 UTC). It
   refuses to run unless `DOCKET_ENV=beta`. It:
   - terminates open workflows;
   - clears documents, sources, events and filings;
   - empties the bucket;
   - keeps the spend ledger, so a reset cannot lift the daily cap.

   It then reseeds a starting queue of 12 seed-11 documents in replay mode, from the live
   recordings, at zero model cost.
6. **Beta refusals at startup:** the app will not start in beta without invite codes, with
   a failpoint variable set, or with the webhook secret empty but `/inbound` enabled.
7. **`compose.prod.yaml`:**
   - the limits above and `restart: unless-stopped`;
   - healthchecks on every service;
   - every port on `127.0.0.1`, with only the API published;
   - the Temporal UI not published;
   - settings from `.env.beta`, never committed, with a `.env.beta.example`.
8. **Runbook** in the README:
   - the Caddy site block, with a request-size limit;
   - how to start, check, turn off and reset;
   - how to back up the volumes.
9. **ADR-009** on hosting: the dev server choice, the measured memory, and what a
   production setup would need instead (labelled "not measured").
10. **Design:** the orchestrator runs `/impeccable` to critique the invite page and the
    sample button after the build, before `/pr-review`.

**Last step — deploy to Atlas: needs go-ahead.** Nothing on Atlas changes before Cristian
says go.
1. Check Atlas's free RAM and disk.
2. Copy the release, fill in `.env.beta` on Atlas, and start it.
3. Add the Caddy site block and reload Caddy. Ledgerline's block stays untouched.
4. Check:
   - `/healthz` answers over HTTPS;
   - from outside, a page without a code is refused;
   - the kill switch turns live answers off and back on;
   - one reset runs by hand.

**Acceptance:** D32–D38.

## Acceptance

v0.1's D1–D17 still hold. `./verify.sh` stays the one gate and runs with no key.

**PR1**
- **D18** Request shape (test, fake HTTP):
  - the model id, `system` and one user turn;
  - the JSON schema with refs inlined and `additionalProperties: false`;
  - thinking off, effort `low`, and no sampling settings.
- **D19** Each unreadable reason (refusal, cut, not JSON, schema) is charged from usage,
  recorded with its reason and raw text, and raises the same error in replay (tests).
- **D20** Retries: 429, 529, 5xx and refused connections retry with backoff. A timeout after
  sending is charged at worst case and not retried. A schema-refusal 400 switches to JSON in
  the prompt, logs it once, and records the mode (tests).
- **D21** Cost per call matches tokens × the price snapshot. The worst-case reservation uses
  the snapshot, and a cost above the reservation raises (tests).
- **D22** A sentinel key never appears in logs, error text, recordings or reports (test).
  detect-secrets stays clean.
- **D23** ADR-008's predictions are committed in a commit before PR1's review. A live report
  cannot mix live and simulated answers, and carries the live banner (test).

**PR2**
- **D24** The spike's result (schema accepted, or fallback) is recorded in ADR-008.
- **D25** The live pass meets G1–G4. It asked each of the 440 requests once, plus only
  no-answer resends (counted from the recordings).
- **D26** The seed-11 report has everything in Measures. CI replays it from the live archive
  with no key and gets the same results (`reproduce_eval.py`).
- **D27** The simulated archive is in `evals/history/simulated/`, unchanged and labelled, and
  its pack check passes. No figure in the README or ADRs is simulated unless it is labelled
  as history.
- **D28** The cost summary is committed. A test recomputes it from the recordings and the
  price snapshot to within $0.000001.

**PR3**
- **D29** The page shows the model id, run date and price snapshot date. Its cost figures
  equal the cost summary (Playwright).
- **D30** The value formula has a unit test with known inputs, and the page shows it.
  Defaults are labelled as examples, and the synthetic auto-file rate is labelled as such.
- **D31** The page is keyboard usable with labelled sliders. It works at phone width with no
  sideways scroll, and respects reduced motion. Confirmed `/impeccable` findings are fixed.

**PR4**
- **D32** The invite gate covers every page and route except `/healthz`. The wrong-code limit
  works, the cookie flags are set, and the app refuses to start in beta without codes (tests).
- **D33** In beta, upload and webhook are refused. The sample button makes a fresh synthetic
  document that runs through the real workflow. The per-address and daily sample caps return
  503 (tests).
- **D34** The kill switch makes zero model calls (gateway call count) and returns 503. The
  daily spend cap returns 503 once reached (tests).
- **D35** The reset clears everything but the spend ledger, refuses outside beta, and
  reseeds with zero model spend (test on compose).
- **D36** `compose.prod.yaml` has the limits, loopback ports and healthchecks, and starts
  healthy locally. The load script's measured peak memory is recorded, and stays under the
  3.3 GB ceiling.
- **D37** In beta, failpoints and the reviewer-token bypass are refused (tests). No secret is
  in the image or the repo.
- **D38** *(needs go-ahead)* The deployed `/healthz` passes over HTTPS. The invite gate,
  the kill switch and a manual reset are checked on the live host.

## Out of scope

- Letting the model decide automatic filing, or changing the two-signal rule or the threshold.
- Any model other than Haiku 5.5, and any frontier model. A gate miss stops the work.
- Tuning prompts on seed 11, or a second live pass to improve the numbers.
- Removing the OpenRouter path.
- Prompt caching and the Batch API. The whole pass costs cents.
- Real or uploaded documents in the beta, email intake in the beta, and OCR.
- A production Temporal setup (Temporal Cloud, or Temporal on Postgres), high availability
  and backups beyond copying volumes.
- User accounts. The beta has one shared demo company and invite codes.

## Decisions (Cristian, 2026-11-12)

1. **SDK:** add the `anthropic` SDK.
2. **Key:** use the Firstcall Anthropic key, not a new one. Spend is held by the $5 cap
   and the daily ledger.
3. **Beta model:** live, with a $1.00 daily cap and the kill switch.
4. **Beta address:** `docket.cristianortega.dev`.
5. **Calculator:** open locally without the reviewer token.
6. **Temporal in the beta:** the dev server with a SQLite file, labelled as not a
   production setup.
7. **Reset time:** 08:00 UTC.
8. **Live pass:** seeds 7 and 11.
