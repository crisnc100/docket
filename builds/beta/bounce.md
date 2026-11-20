The plan's safety design is sound in outline, but several controls are unspecified or untested at the point where they do the work. Four acceptance items cannot pass as written (A1, B1, B4, B5). I read the code and hashed a few corpus files; I ran no tests, and Caddy's header behaviour (gap 6) is from memory.

## Money, the gate and writes

1. **The $1 cap fails open.**
   - The code default is $1.50 (`docket/settings.py:28`), and the worker is the process that applies it (`docket/runtime.py:68`).
   - The current compose anchor does not pass `DOCKET_DAILY_BUDGET_USD` at all (`compose.yaml:54-66`), so one omission in `compose.prod.yaml` gives a silent $1.50.
   - No startup refusal covers a missing or higher cap, C2 checks only ports and memory, and B4 never names the amount.

2. **The ledger is the only limit on a shared key.**
   - It is Docket's own arithmetic from a price snapshot (`docket/llm/budget.py:59-63`, `config/model_prices.yaml:4`).
   - The key is shared with Firstcall (`builds/v02/plan.md:423`), and the plan sets no limit at the provider.
   - A ledger bug or a leaked `.env.beta` spends outside this app, and revoking the key breaks Firstcall.

3. **B5's "no restart" kill switch cannot work as an environment variable.**
   - Settings are read once at process start (`docket/runtime.py:20`), and a running container's environment cannot change.
   - A test that patches settings will pass while the real switch needs the API and worker recreated.
   - The plan does not say what a document does when the switch flips mid-workflow.
   - Rev 2's check that the switch makes zero model calls (D34, `builds/v02/plan.md:396`) is gone.

4. **The reviewer token stays a second way in.**
   - "Extends `ReviewerAccess`" leaves the Bearer path (`docket/review_access.py:68-71`), which has no wrong-attempt limit.
   - Rev 2's D37 refused it in beta (`builds/v02/plan.md:403`); rev 3 drops that, and A1 never sends a Bearer token.
   - `.env.beta.example` lists "every setting by name", which includes `DOCKET_REVIEW_TOKEN` (`.env.example:26`).
   - With the token unset, the 503 branch (`docket/review_access.py:62-67`) fires before any cookie check.

5. **Invite codes and the cookie are underspecified.**
   - `DOCKET_INVITE_CODES="abc,"` splits into a valid empty code; the refusal only covers "empty", with no minimum length and no refusal of the example's placeholders.
   - The "keyed hash" names no key, and none is in the settings list.
   - A cookie holding only a hash carries no timestamp, so "expires in 7 days" is a browser hint. A copied cookie works until the code is removed, and A2 does not test expiry.

6. **The plan does not say which `X-Forwarded-For` hop is read.**
   - If the app reads the leftmost and Caddy appends, a stranger gets unlimited code guesses and skips the per-address sample cap.
   - A3 tests only the unset case, never a client-supplied prefix.
   - If `DOCKET_TRUST_PROXY` is lost in production, everyone shares one address. Ten wrong codes from a stranger then lock out every invitee, since A3 refuses "even with a right code".
   - Where the lockout count is kept is unstated; the existing limiter is in memory (`docket/webhook.py:48-66`).

7. **B6 is false as written: visitors can still write their own bytes.**
   - The dismiss reason is free text with no length cap (`docket/api.py:214-232`).
   - It is stored as the status reason and in Temporal history (`docket/workflows.py:168-169`, `docket/db.py:36`).
   - The app's body limit covers only two paths (`docket/webhook.py:17`), so the Caddy limit in a README is the only bound.

8. **The 403s sit behind a 10 MB read, and the public list is wrong.**
   - `/inbound` is public in the middleware (`docket/review_access.py:44`), and `BodyLimit` buffers the body before any handler runs (`docket/webhook.py:28-40`).
   - A handler-level 403 lets a stranger with no code make a 512 MB container buffer 10 MB per request.
   - A1 expects 401 and A5 expects 403 for the same request.
   - The code-form POST must be reachable without a cookie, so it is a fourth public route against A1's "public three".

9. **Cookie sign-in adds cross-site request risk the Bearer header never had.**
   - SameSite=Strict treats every `*.cristianortega.dev` as same-site, including whatever else Atlas serves.
   - `POST /review/...` and `POST /beta/samples` get no Origin check, and the cookie has no `__Host-` prefix.

## Things that will break

10. **The seeded queue is empty an hour after each reset.**
    - Held documents expire after 3600 seconds by default (`docket/settings.py:39`, `docket/workflows.py:160-165`), and the plan never raises it for beta.
    - From 09:00 UTC, visitors see "Nothing waiting". Any invitee can also clear the shared queue.
    - C1 counts 12 reseeded documents, not 12 still waiting.

11. **The generator cannot make one document of one case type.**
    - `generate` writes all 180 files to disk (`docket/corpus.py:146-289`).
    - A per-document refactor risks shifting the random draws that the committed corpus and the 440 recordings depend on (`docket/llm/replay.py:30-32`). That is a change to `main`, not a beta-only one.

12. **Several case types contradict B1 and B2.**
    - Four case types are byte-identical for every seed (`docket/corpus.py:57-58`, `:249`, `:253`, `:256`). I confirmed the same hashes in seed-7 and seed-11 for three of them.
    - Ingress dedupes on the hash and returns the old document with no new workflow (`docket/ingress.py:76-78`, `:144`).
    - Four case types end `rejected` (`docket/corpus.py:239-241`), so B1's "filed or held" fails on types B2 must accept.
    - `exact_duplicate` needs its partner document (`docket/corpus.py:257-260`).

13. **B4 contradicts the code.**
    - A budget refusal in classify ends the document `failed` (`docket/workflows.py:177-186`); only unreadable answers are held (`docket/activities.py:123`).
    - Meeting B4 needs an unlisted activity or workflow change, which also changes local behaviour.

14. **The sign-in page needs unlisted work and has no browser test.**
    - The page and app are built on a Bearer token in session storage (`web/login.html:64-108`, `web/src/main.ts:49-61`); JavaScript cannot see an HttpOnly cookie.
    - The browser tests reach the API at `http://<container>:8000` (`tests/browser/test_review_browser.py:73`), where Chromium drops Secure cookies.
    - The first real browser sign-in would be in production.

15. **The front page link has no mechanism.**
    - `/` is a static file under a CSP with no inline script (`docket/api.py:248-266`), and `DOCKET_ENV` is a runtime setting.
    - Any fix needs templating or another public route.
    - The nav already links `/reviewer` in both modes (`web/public/home/index.html:21`), and line 67 still says the hosted beta is "not built yet".

16. **Startup refusals name only "the app".**
    - Failpoints fire in the worker (`docket/activities.py:147`, `:270`) and are read from the raw environment (`docket/failpoints.py:5`).
    - A6 does not say that the worker and the reset command refuse too.

17. **Sample caps are tested one request at a time.**
    - B3 would pass a check-then-increment that 50 parallel requests defeat; the ledger's atomic update is the model (`docket/llm/budget.py:140-145`).
    - Per-address rows are kept across resets, so addresses accumulate indefinitely.

18. **The reset races in-flight samples, and nothing checks the 08:00 run.**
    - Activities hit a missing row and retry (`docket/core/ledger.py:84-85`).
    - No item verifies that the scheduled run fires or catches up after downtime.

19. **The load run cannot work as described.**
    - Sixty samples in ten minutes from one address breaks the plan's own 20-an-hour cap.
    - In replay, fresh seeds miss (`docket/llm/replay.py:109-110`), so it measures failures; live needs a key and spends.
    - A ten-minute run also misses the worker keeping one gateway and HTTP client per document, never evicted (`docket/runtime.py:36`, `:52-54`).

## The three-PR split

20. **`main` should keep working locally, with these caveats.**
    - The quoted approval, "Yes, after each merge", does not obviously match a deploy only after C.
    - B's generator refactor and B4 change touch shared code, and B needs a migration it does not list.
    - C's Dockerfile change is shared with CI (`verify.sh:21`). The browser stage needs root (`Dockerfile:10-14`), and digest pins must cover both Apple Silicon and the Atlas host.
    - Nothing runs the beta composition end to end until C2.
    - "The rest of rev 2 stands" conflicts with a renumbered bar that drops D34 and D37.

## Operations

21. **Smaller gaps on a shared host.**
    - The compose anchor gives the Anthropic key to the internet-facing API and to `migrate` (`compose.yaml:54-66`).
    - C2's `docker compose config` prints secrets.
    - There are no CPU limits or log rotation, no `caddy validate` or rollback step, and no alert on spend or a failed reset.
    - Rev 2 says Atlas runs Ledgerline and rev 3 says Askwell, so the 14 GB figure may be stale.
