## 1. SCOPE VERDICT

**Under-scoped** — the direction (private per-browser sandbox, no sign-up, own records) is right, but three things it depends on are missing: containing the PDF reader, isolating documents that are currently identified by their content hash, and deleting from every place the text is stored.

## 2. ADVISORY

**Must-have before uploads open**

1. **Run PDF extraction in a child process with hard limits.** I measured `extract_document` on crafted one-page PDFs: a 22 KB file took 22 s and 470 MB, and a 73 KB file was still running at 45 s with over 1 GB.
   - The planned 10 MB and 20-page limits stop neither.
   - The worker is capped at 768 MB (`compose.prod.yaml:164`) and the step runs in a thread that cannot be cancelled (`docket/activities.py:84`), retried five times (`docket/workflows.py:67`). One upload can take down the only worker.
   - Push for: a subprocess with a memory limit (about 300 MB), a CPU limit (about 10 s), a wall-clock kill, and an empty environment so it holds no model key or database password. Also count pages before extracting, cap extracted text at about 200,000 characters, and allow one attempt only.
   - Caveat: I ran this on the host's pypdf 6.14.2; the lock pins 7.0.0, so rerun it in the image.

2. **Make the sandbox part of a document's identity.** Today a document is unique on `(mode, run_id, content_hash)` (`docket/db.py:29`) and the bytes live at `raw/<sha256>` (`docket/ingress.py:66`).
   - Two visitors uploading the same PDF would get the same document id, and one's "Delete now" would remove the other's file.
   - Add a sandbox id to the unique key and prefix every storage key with it.
   - No route checks ownership today (`docket/api.py:209`, `:236`, `:258`, `:303`). Route them all through one lookup that takes sandbox and document together and returns 404, with one test that tries every route using a second cookie.

3. **Delete from everywhere the text actually is.** The direction's list misses two places.
   - Temporal: the full extracted text travels in the workflow's stage through every step (`docket/activities.py:76`), so it sits in Temporal's history file. The nightly reset terminates workflows but does not delete histories. Keep text out of the workflow (store it under the sandbox prefix and pass the key), or delete the workflow execution on expiry.
   - The filed copy: approving writes a second copy of the PDF (`docket/activities.py:260`). For sandbox documents, skip it and show where it would be filed.
   - Held documents wait 86,400 s in beta; sandbox ones need an hour at most. The existing `reset` service can run the sweep every few minutes.
   - Model exchange records need no work: live calls only record synthetic-corpus sources (`docket/llm/gateway.py:333`).

4. **Put the caps where they bind.** A "5 per sandbox per hour" limit is not a control, because clearing cookies makes a new sandbox.
   - The per-address and global daily caps are the real limits. Count an IPv6 address by its /64, or one host has unlimited addresses (`docket/beta.py:134`).
   - Check the caps before reading the body.
   - Cut the upload limit to 2–3 MB. The API holds several copies of each body in memory (`docket/webhook.py:32`, `docket/api.py:191`) in a 512 MB container, and Docket has no OCR, so large scanned PDFs yield nothing anyway.
   - Raise Caddy's 32 KB limit for the upload path only.

5. **Add an uploads-off switch.** The current kill switch stops model calls only; the parser runs before the model. One more row in `beta_flags` covers it.

6. **Make the privacy copy true.**
   - "Not kept" by the provider is likely false: Anthropic's API normally retains inputs for a limited period. Check the current terms and say "not used for training; the provider may hold it briefly".
   - ADR 009 mentions a nightly `pg_dump`; a dump or host snapshot of Docket's volumes would break the one-hour promise.
   - Tell visitors with a scanned PDF "this PDF has no text layer" rather than "no matching unit".

**Already adequate — add nothing**

- **Cost:** the budget reserves the worst case before each call, and startup refuses a cap above $1.00 (`docket/beta.py:72`). Abuse can pause the demo for a day, not run up a bill.
- **Prompt injection and model-proxy abuse:** the output is one of five categories plus a shortlisted id, capped at 256 tokens, and never files.
- **Stored XSS:** the page writes with `textContent` under a nonce CSP (`docket/review_access.py:50`), and sandbox content is only shown to its author. Add one browser test with script-like record text.
- **CSRF:** keep the Origin check on every non-GET (`docket/review_access.py:185`) and the `__Host-` cookie. The Origin check is the real control, since Askwell and Firstcall are same-site, so `SameSite=Strict` does not stop them.

**Viewer (decision 3)**

Show the PDF from the file the browser already holds, as a local blob URL, and never serve uploaded bytes back. The page's CSP already allows `frame-src blob:`. This removes the file-hosting risk entirely; the cost is no preview after a reload.

**Record list (decision 4)**

It is worth it and safe if constrained. Without it no candidate is found, the model is never called (`docket/workflows.py:128`), and every real document ends at "no matching unit".

- Use a small form (1–10 rows of address and tenant names, about 120 characters per field), not free-paste parsing.
- The server assigns the record ids and the building slug. `Record` requires `UNIT-dddd` (`docket/config.py:28`), and storage keys stay safe.
- The choose prompt sends only ids (`docket/prompts.py:27`), so pasted text never reaches the model.

**Cut**

- CAPTCHA or Turnstile for v1.
- Per-sandbox hourly limits.
- A separate container for PDF parsing or sanitising.
- Disk quotas — the count caps times 3 MB bound it.
- The server-side file route for sandbox documents.
- HMAC on the cookie — a 128-bit random id looked up server-side is enough.

**Sequencing**

Ship in three steps, each releasable: sandbox isolation and deletion with the existing samples; then uploads with the contained parser; then the record form.

## 3. QUESTIONS

1. **Real leases carry other people's personal data. Accept them with a plain notice, or ask for redacted documents only?** Recommend: accept, with one line beside the upload button saying what happens to the file and suggesting a non-sensitive document.
2. **Without a CAPTCHA, one determined person can exhaust the day's budget and pause the demo for everyone until midnight UTC, at a cost of at most $1. Acceptable?** Recommend: yes for v1; add Turnstile only if it happens.
3. **Is Atlas backed up (a `pg_dump` or host snapshots) in a way that captures Docket's volumes?** Recommend: exclude Docket's volumes, since nothing in them is worth restoring.
4. **How tight should the per-address caps be, given evaluators behind one office address share them?** Recommend: 5 uploads an hour and 15 a day per address, 100 a day overall.
