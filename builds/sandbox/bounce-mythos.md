# Adversarial review: `builds/sandbox/plan.md` (rev 1)

I read the three documents and checked the plan's claims against the code on branch `sandbox`. Nothing was executed, so anything about Temporal, SQLite, Caddy or kernel behaviour is from my knowledge and marked "confirm". The plan's code citations are accurate, and the advisor's must-haves are all adopted. The gaps below are what remains.

## Must-fix before build

1. **Unbounded sandbox creation can starve the sweep and break the one-hour promise.**
   - `POST /sandbox` is public, and a cap on it is explicitly out of scope (plan:195). The only check is `Origin` (`docket/beta.py:145`), which curl can set.
   - The plan deletes per sandbox: Temporal deletes, one S3 prefix listing, then rows. Each run is capped at 3 minutes, and nothing says what happens when the cap is hit.
   - A loop creating hundreds of thousands of empty sandboxes makes every run time out, so real visitors' files outlive 55 minutes.
   - Missing: a per-address cap, or creating the row only on first write; one bulk statement for empty sandboxes; oldest-first ordering; uploads refused while a backlog exists.

2. **The 55-minute bound is arithmetic, not behaviour, and S6 cannot catch a bad build.**
   - "A test pins the three constants against 60 minutes" (plan:138) passes even if the sweep never finishes.
   - The order "Temporal first, then rows, then objects" (plan:136) means that with Temporal down or slow, nothing is deleted from Postgres or S3 either.
   - The sweep replaces `reset`, which `depends_on` a healthy worker (`compose.prod.yaml:179-181`). If the worker crash-loops on `compose up`, the sweep never starts.
   - Nothing fails closed. Alerts are out of scope, and `/healthz` fields that nobody reads are not a control.
   - Needed: per-store independence, no dependency on the worker, and the API refusing samples and uploads when `last_sweep_age` exceeds a few minutes.
   - Needed tests: sweep with Temporal stopped, sweep with a backlog larger than one run, sweep killed mid-run.

3. **PR 3 contradicts "no workflow code changes", and R4 cannot pass.**
   - R4 requires that unit ids "appear nowhere in the Temporal history" (plan:296).
   - The shortlist activity returns `valid_ids` and `candidates[].record_id` in the stage (`docket/activities.py:98-104`).
   - The workflow stores them (`docket/workflows.py:127`) and validates approve against them (`:76`). The approve update carries the id as its value (`docket/api.py:336`).
   - Either R4 drops "ids", or the workflow changes and ADR 002 applies.
   - The file activity also needs changing: it looks the chosen unit up in the shipped list (`docket/activities.py:251-253`) and raises `StopIteration` for `UNIT-9001`. PR 3's phases do not list it.

4. **Re-uploading the same PDF returns the stale result, which breaks the main PR 3 flow.**
   - Within a sandbox, the same bytes give the same document (`docket/ingress.py:77-93`), and a terminal document is not restarted (`:150`).
   - The plan also says "a document already read keeps its candidates if the list changes" (plan:284).
   - The natural path is: upload, see "no matching unit, add units", add units, upload again. That returns the old answer and still spends one of 5 hourly slots.
   - The only way out is "Delete now" and retyping everything. R1 does not test this sequence.

5. **The 200,000-character cap is too high for Temporal, and one upload may be parsed five times.**
   - The full `text` travels in the input and output of every activity (`docket/activities.py:76`), about 12 copies per document.
   - Temporal's default payload limit is 2 MB (confirm). The Python converter escapes non-ASCII, so 200,000 astral characters is about 2.4 MB.
   - The extract result is then refused, the activity is retried up to 5 times (`docket/workflows.py:68`), and the document ends `failed`. That contradicts U4's "one attempt".
   - Matching already refuses anything over 64,000 characters (`docket/core/candidates.py:27`), so cap at 64,001 characters with a byte bound.
   - "Counts times 3 MiB" (plan:270) ignores this amplification into `temporal.db`, which never shrinks.
   - U3 tests the cut, not that a maximal non-ASCII text survives the workflow.

6. **PR 2 never says what uploads are matched against.**
   - The shortlist change is listed only in PR 3 (plan:281). As written, PR 2 uploads go through `load_records()` against the 200 synthetic units (`docket/activities.py:91`).
   - A real document containing "UNIT-0042" or a common surname gets candidates, the model is called, and it could auto-file to a synthetic unit.
   - Decision 2's claim "the model is never called" (plan:492) is then false. PR 2 needs an explicit empty list for uploads and a test.

7. **The Caddy deploy checks do not prove what they claim (U12).**
   - Step 4 runs while uploads are off (plan:400-404; they turn on in step 5). The 3.5 MB POST gets a 503 or 401 "before any body is read", so it says nothing about whether Caddy passes 3.5 MB.
   - "A 40 KB POST to `/review`" is ambiguous. Docket also answers 413 above 16 KB (`docket/review_access.py:95`), or 401/403 first without a cookie or `Origin` (`:181-186`). `POST /review` is not a route.
   - A proper check: a valid 2.9 MiB PDF accepted end to end through Caddy with a real cookie; a 4.5 MB body refused by Caddy (check the `Server` header); 40 KB to a real POST route refused by Caddy.
   - `request_body` only wraps the reader, so confirm the status code your Caddy version returns.

## Should-fix

8. **The marker test (S7, U10) has a positive control for only one store.**
   - Only the SQLite file is checked "before deletion". PDF content streams are normally Flate-compressed, so a grep of S3 or SeaweedFS data never finds the marker and passes vacuously.
   - No Postgres row holds document text, so the `pg_dump` grep is vacuous too. Use the dismiss reason (`docket/workflows.py:169`) and unit text as the Postgres markers.
   - Temporal's SQLite probably runs in WAL mode (confirm). Old frames holding the text stay in `temporal.db-wal` after `secure_delete` zeroes the main file. Grep the whole `/data/server` directory.
   - "In no log" should name all six containers' docker logs plus Caddy and journald.

9. **Core dumps of the parser child can land on the host.**
   - A CPU-limit or file-size-limit kill dumps core by default. `core_pattern` is host-wide, so with `systemd-coredump` or apport the child's memory, including the document text, is written to the host.
   - No sweep reaches it. Set `RLIMIT_CORE=0` in the child and assert it in U2.

10. **The daily cap is trivially exhausted, and per-address limits barely bind on IPv6.**
    - Slots are taken before the body and kept on failure (plan:233-237). 100 one-byte requests end uploads for everyone until midnight UTC.
    - Home and VPS IPv6 allocations are commonly a /56 or /48, which is 256 to 65,536 separate /64s.
    - Cristian accepted this risk for the model budget; the plan should state it for the upload counter too.
    - Cheap mitigations: a global hourly cap so an outage lasts an hour, and returning the slot when the body is not a PDF.
    - Uploads and samples share the $1 budget, so upload abuse also pauses the existing sample demo.

11. **Nothing limits concurrent uploads or how long a body may take.**
    - The gate's 16 KB buffering is bypassed for this path, and `BodyLimit` does not cover it (`docket/webhook.py:18-21`).
    - Up to 100 bodies of 3 MiB can be in flight at once. Each exists as a bytearray, a bytes copy and the boto3 body, inside a 512 MB API container (`compose.prod.yaml:136`).
    - Add a small semaphore (2 to 4) and a read deadline of about 30 s. U5's never-ending body tests refusal by count only.

12. **"One parse at a time" lets five slow PDFs fail everyone else's documents, samples included.**
    - A wait plus a retryable error burns the 5 attempts (`docket/workflows.py:65-69`) in about 15 s of backoff, while five 15-second parses take 75 s.
    - Queued documents end `failed / retries_exhausted`. The extract path is shared "in every mode", so samples fail too.
    - If the wait blocks inside `asyncio.to_thread` (`docket/activities.py:84`), it can exhaust the default thread pool of about 5 on a small VPS and stall every activity.
    - The child shares the worker's `cpus: 1.0` (`compose.prod.yaml:165`), so `nice` it.
    - U1 only sends a sample after the crafted file. Test five crafted uploads at once with a sample alongside.

13. **With visitor units, the model's suggestion is meaningless.**
    - The choose prompt sends only ids (`docket/prompts.py:18-28`), and `UNIT-9001` to `UNIT-9010` never appear in a real lease.
    - The model sees document text plus opaque ids and cannot match. The visitor's text still goes to Anthropic, and the page shows a suggestion with no information behind it.
    - Either skip `choose` for uploads and show only the category, or send unit text. Sending unit text changes the privacy line and the injection surface.

14. **Late writes after "Delete now" are left to the sweep.**
    - The object is written before the guarded transaction (`docket/ingress.py:67` then `:72-75`), and the workflow starts after commit (`:148-151`).
    - An upload with a slow body can write `sbx/<deleted>/…` after "Delete now" has answered. A workflow can also start for rows that are already gone.
    - The data then lives up to 5 more minutes after the visitor was told it was deleted. Ingress should delete its own object when the guard refuses.
    - S5's "leaves nothing" must be asserted immediately, not after the next sweep.
    - An in-flight `classify` or `choose` still sends text to Anthropic after deletion. That is unavoidable, but the docs should say so.

15. **The crafted-PDF set is the author's own four cases, and no check guards against false rejections.**
    - The committed corpus is tiny synthetic files. A build whose limits reject most real 20-page PDFs passes U1 to U12.
    - Add realistic public PDFs (dense text, fonts, forms, about 3 MiB) to the measurement and to a must-pass test.
    - Also bound what the parent reads from the child's pipe.
    - The parent must return only a reason code. Today the chained pypdf error, which can quote file bytes, goes into the Temporal failure and worker logs (`docket/activities.py:69-72`).

16. **There is no rollback for the PR 1 migration.**
    - Once the unique key changes, the old image's `ON CONFLICT (mode, run_id, content_hash)` (`docket/ingress.py:83`) fails on every ingest.
    - The old code also needs the deleted `DOCKET_INVITE_CODES` and `DOCKET_SESSION_KEY`.
    - The plan needs a tested downgrade, a kept copy of `.env.beta`, and the brief window of old containers on the new schema during `compose up` noted.

17. **The compromised-child model is inconsistent.**
    - The plan hides the environment from the child (plan:218), but the child keeps network access to `temporal:7233`, which has no authentication. It could read every visitor's history.
    - Either drop the claim or cut the child's network (`unshare` or seccomp).
    - `compose.prod.yaml` also has no `pids_limit`, `cap_drop`, `no-new-privileges` or `read_only` on the worker.

18. **The privacy line omits stored addresses.**
    - `upload_sends` keeps the visitor's address for 24 hours, and `sample_sends` for an hour (`docket/db.py:108-115`).
    - Either store a keyed hash or say so in the notice.
    - SeaweedFS keeps deleted bytes until compaction, which the plan itself marks as unverified (plan:485). Verify it before the copy ships.
    - The notice sits only beside the upload button; the PR 3 units form collects third parties' names.

19. **Sandbox expiry is hostile to an evaluator who takes their time.**
    - At minute 45 writes close with "Delete it now to start a new one". At 50 everything, including typed units, vanishes.
    - There is no countdown, renewal or automatic rollover. Keep the units in the browser and reapply them, and roll over automatically.
    - If cookies are blocked, every load makes a new empty sandbox with no explanation.

20. **The ownership test discovers routes by a path parameter.**
    - Routes scoped by cookie alone (`GET /review`, the units routes, "Delete now"), or taking an id in the body, are not caught by "a new route … fails it" (plan:107).
    - Require every beta route to be classified as public or sandbox-scoped, and fail on any unclassified route.
    - The file route's hard-coded `raw/{hash}` (`docket/api.py:245`) must move to the sandbox key. S4 should fetch both previews.

21. **The upload preview trusts the browser's file type.**
    - `createObjectURL(file)` for a non-PDF, such as HTML or SVG chosen via "All files", renders in Docket's origin inside the iframe and the "Open PDF" tab (`web/src/main.ts:246-253`).
    - Scripts are blocked by the inherited CSP, but `form-action` is not restricted.
    - Check `%PDF-` in the browser, re-wrap the bytes as `application/pdf`, and add a browser test.

## Notes

22. Caddy's path matcher is case-insensitive and cleans paths, so `/SANDBOX/DOCUMENTS` and `//sandbox/documents` get the 4 MB allowance. The app's gate still refuses them at 16 KB, but add a test. The plan replaces the block wholesale; first confirm the live block has nothing beyond the README's version (`README.md:250-255`).
23. A maximal units form in 4-byte characters is 19.2 KB, over the 16 KB cap (`docket/review_access.py:21`), and gets a generic 413.
24. The server-assigned building code must be a string that cannot occur in text. `Signal.BUILDING` is a phrase match (`docket/core/candidates.py:376`).
25. PDFs with only an owner password are rejected as `encrypted` (`docket/core/extraction.py:67`). This is common for real leases and needs a clear message. Scans with a few stray characters miss the `characters: 0` hint.
26. `/healthz` publicly exposing `oldest_sandbox_age_seconds` leaks activity; consider serving it on localhost only.
27. Hostile text goes out under Cristian's Anthropic organisation. Usage-policy enforcement is per organisation and could affect Askwell and Firstcall.
28. A five-per-hour cap is shared by everyone behind one office NAT, the intended audience, with no number shown. This is approved but worth watching.

## Checked and found sound

- Live exchange records are written only for `synthetic_corpus` sources (`docket/llm/gateway.py:327`).
- The matcher is linear for attacker-chosen units; aliases are escaped (`docket/core/candidates.py:402`).
- The `Origin` check and `__Host-` cookie cover CSRF and sibling-subdomain fixation.
- The budget cap holds.

**Verdict: needs changes.**
