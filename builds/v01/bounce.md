**Verdict: not ready to build.** Seven design gaps let a build pass every criterion while one of the three portfolio claims is false. The worst is that the plan's own injection case can be auto-filed to the attacker's record without the LLM being involved.

Line references are to `plan.md`. Items marked (U) are library behaviour from memory, unverified this session.

## Gaps that falsify a claim

1. **Injection wins through the deterministic path (:50, :60, :65).** The injection text contains a well-formed ID, and rule 1 of the candidate ladder treats an explicit ID in the body as the strongest signal.
   - An address-only document plus an injected ID has one explicit ID, so the conflict guard (:64, "two distinct strong explicit IDs") never fires.
   - Corroboration is undefined beyond its three tiers, so nothing stops that ID from rating `strong` and being filed.
   - D4 only asserts "not outside the shortlist", which is true by construction, so it passes while the document is misfiled.
   - If `UNIT-0001` isn't in `records.csv`, the case is harmless and tests nothing.

2. **Dedup rests on the Temporal workflow id (:72), and "the reuse policy" is never named.**
   - (U) The default allows a new run once the first closes, and any policy stops holding after namespace retention.
   - `filed_documents` is unique on (content_hash, record_id, category) (:33), so a resubmission that is classified or chosen differently files a second time.
   - `ON CONFLICT DO NOTHING` (:67) then hides the conflict.
   - D6 only tests three concurrent submits, never a resubmit after filed, dismissed or expired.

3. **Observe mode collides with act mode (:76, :97).** Both use `doc-<content_hash>` and the same `documents` row.
   - A document that reaches a terminal status in observe mode can never be filed later, because nothing leaves a terminal status (:67).
   - Eval reruns collide with earlier runs.
   - The plan doesn't say what the review path does in observe mode; if it waits on the signal, the eval hangs until the timeout.

4. **The storage key is not shown to be deterministic (:41).** The sources of `{date}` and `{slug}` are undefined.
   - If either comes from the clock or the LLM, a retry writes a second object.
   - Two different invoices for the same unit on the same date collide, which hits "same key, different hash is an error" (:66) with no terminal status defined.
   - A slug taken from a sender-controlled filename is a path-traversal vector in the local adapter.

5. **There is no failure terminal state (:77).** No `failed` status is specified.
   - (U) Temporal's default activity retry is unlimited, so a corrupt or encrypted PDF, a replay miss or a tripped budget guard retries forever.
   - If `finalize` fails or the workflow is terminated, the row stays non-terminal with nothing to reconcile it.
   - "Nothing is dropped silently" is therefore unproven: D5 runs only over a happy replay eval.

6. **Ingress is outside the exactly-once story (:84–86).**
   - The plan doesn't say where the `documents` row is created relative to workflow start; an API crash between the two is the at-least-once case, and it is untested.
   - `store_raw` as an activity implies PDF bytes travel as workflow input. (U) Temporal payloads cap at about 2 MB.
   - There is no home for a message with zero attachments, a non-PDF attachment, or the same PDF under a second `message_id`.

7. **Claim 3 can't be measured as designed.**
   - `strong|medium|none` (:65) gives at most three points, which is not a curve.
   - There is no floor on auto-file rate, so sending everything to review satisfies "wrong-file count 0" (D10).
   - There is no rules-only baseline, so nothing shows the LLM adds anything over the top-scored candidate.
   - The same author writes the generator, labels, rules and threshold, with no held-out seed.
   - Zero wrong out of roughly 70 auto-filed still allows about a 4% error rate at 95% confidence.
   - Category accuracy isn't scored, although the category sets the destination path.

## Will break during the build

- **Dependency rule (:24):** it stalls on Playwright, pytest-cov, pytest-asyncio, httpx, python-multipart, PyYAML and uvicorn, all implied but unlisted.
- **Ledgerline reference (:35):** "the same design as Ledgerline's" points at code that is not in this repo, so a builder can't follow it.
- **CI criteria:** D1, D10 and D15 say "in CI", but there is no remote and `gh` is logged out, so they can't be verified at build time.
- **Temporal dev server:** (U) it binds to localhost and keeps state in memory by default. Other containers can't reach it, and a restart loses every workflow waiting on review.
- **Corpus determinism (:55):** (U) reportlab embeds timestamps unless told otherwise, and zlib output can differ between macOS and Linux, so the hash test may fail in CI.
- **Replay key (:37):** it includes messages, so any change to prompts, candidate order or pypdf output invalidates every recording. Nobody is named to re-record, when, or at what cost.
- **First live call is in P5:** whether a cheap OpenRouter model honours a per-call enum schema is discovered last. (U) Support varies by provider.
- **Kill tests (:105–106):** they need a deterministic failpoint in the code, which isn't planned. (U) After a SIGKILL the retry waits out the activity timeout, and no timeouts are specified.
- **Phase order:** the headline fault tests land in P5, after the UI. A wrong idempotency design surfaces last.
- **Test infrastructure:** workflow tests and the replay eval need Postgres in CI, which isn't mentioned. (U) The time-skipping test server downloads a binary at runtime.
- **Long-lived review workflows:** they break on the first redeploy that changes workflow code. There is no replay test.
- **Preview link (:92):** (U) a presigned MinIO URL uses the in-network hostname, which the browser can't reach.
- **`review_items` (:34):** no columns are given, and it is a second source of truth beside workflow state.

## Where the acceptance bar is too weak

| Criterion | How a bad build passes |
|---|---|
| D2 | 95% line coverage with a mocked boto3; the `file` activity and workflow are not in scope |
| D4 | Tautological; never checks that injection documents reach the gold record or review |
| D5 | Replay, observe mode and happy path only; no poison document, DB outage or terminated workflow |
| D6 | No check through HTTP, after completion, or with a different `message_id` |
| D7 | Passes with an in-process exception instead of a real kill; no proof the fault fired, and it never checks duplicate events or final status |
| D8 | Misses double approve, approve after expiry and approve with an unknown record id |
| D10 | Passes with a 0% auto-file rate; no minimum per case type |
| D11, D13 | Existence checks only |
| D12, D14 | One happy click; "healthy" is undefined |

## Missed edge cases

- The corpus list (:42–51) has no empty-text-layer, corrupt, encrypted, non-PDF, zero-byte or very long PDF, although P2.1 has a branch for the first.
- The label schema `filed|review` has no outcome for duplicates.
- Near-duplicates (same document, different bytes) bypass hash dedup.
- Tenant-name collisions, former tenants and building-level documents have no defined gold label.
- There is no truncation policy for the text sent to the LLM.
- Event appends on activity retry have no idempotency key, so a trace can show "filed" twice.
- The per-run budget guard doesn't cover a long-lived worker. An exposed unauthenticated webhook can drain the $50.
- Record mode on a real beta document would commit its content to a public repo.
- "Usable free beta" is undefined while auth and deployment are out of scope.

## Over-built for a solo, $50/month build

- **Shadow diff (:109):** the legacy CSV is fabricated with a known 10% error rate, so the result is predetermined. A sharp reader will discount it, and perhaps the rest.
- **Docs quotas (:113–115):** five ADRs with "measured results" and three failure entries invite invention. Three of the five alternatives (hand-rolled lock, Docling, Gmail) are never built, so they can't be measured. The build log also conflicts with your "docs never describe the journey" rule.
- **Next.js, a web Dockerfile and Playwright** for a two-button queue: the largest non-core cost.
- **Three ingestion routes plus a CLI:** the Postmark adapter has no real fixture behind it.
- **Two storage adapters:** this doubles the idempotency surface, and the fault suite only exercises MinIO.
- **One PR (:12):** the stated reason covers P2–P3 only. P1, the UI and the docs each stand alone, and a single review of everything will not scrutinise the `file` activity properly.
- **"Exactly-once" wording:** the design delivers at-least-once execution with idempotent effects, and an interviewer will press on the difference.
