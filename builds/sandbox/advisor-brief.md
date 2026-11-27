# Advisory brief: Docket "try your own document" public sandbox (security review)

## THE ASK
Cristian (the owner) wants people evaluating Docket as a product to try it on THEIR OWN documents in the easiest way possible: no sign-up, no invite code, nothing they have to ask him for. He explicitly wants it "completely safe, but easy for the user", and asked for a security-focused advisor on the design before anything is built. This is a portfolio/demo product (open source, synthetic data), hosted at https://docket.cristianortega.dev on a single VPS (Atlas) that also hosts two other demo apps (Askwell, and soon Firstcall). His standing public-demo rules (decided 2026-11-13): no invite codes; limits stay hidden until hit, then "try again tomorrow"; anyone may upload data.

## WHAT WE KNOW (from the repo at /Users/cortega/Documents/Projects/docket/docket-trunk, main 01cefeb, which is what is deployed)
- Docket files property PDFs (leases, notices, invoices) to one of a list of known records ("units"). The shipped list is 200 synthetic units. A PDF goes in by upload (POST /documents) or a signed email webhook (POST /inbound); Docket saves the original bytes, starts a Temporal workflow, extracts text with pypdf (docket/core/extraction.py), finds up to five candidate records with regex/scoring (docket/core/candidates.py, scoring.py), rules decide auto-file vs review, and a language model (Anthropic claude-haiku-5-5 via docket/llm/) labels the category and suggests a shortlisted record or none. The model's suggestion is shown beside the rules' pick; the model never files on its own.
- Today's hosted beta (DOCKET_ENV=beta, docket/beta.py, docket/beta_samples.py, docket/beta_reset.py, docket/api.py): the review page /reviewer and every API route need an invite-code session (HMAC-signed cookie); visitors cannot upload; they press "Send a sample document" which makes a server-generated synthetic PDF; per-address and daily caps; a kill switch; a $1.50/day budget setting (docket_daily_budget_usd) with a spend ledger in Postgres; a nightly reset reseeds the demo. Everyone with an invite shares ONE queue and anyone can approve/dismiss any document. The 10 MB request limit applies to uploads. Wrong-code lockout per address. Caddy terminates TLS and reverse-proxies to 127.0.0.1:8001 with request_body max 32KB for the beta block (so uploads are currently blocked at the proxy).
- docs/operations.md has the beta runbook; docs/product-brief.md the product.
- A real visitor PDF will not match the 200 synthetic units, so against the shipped list it would always end at "waits for a person, no matching unit" — useless as a demo. That is why the design lets a visitor paste their own short record list.
- Model calls go to Anthropic's API (no training on API data by default). Document text is untrusted input to the prompt (prompt injection possible), but the model's output is only a suggestion constrained to the shortlist.

## CURRENT DIRECTION (attack this)
Replace invite codes and the shared queue with a private, anonymous, short-lived sandbox per browser:
1. "Try your own document" on the front page: a cookie (random, HMAC-signed, HttpOnly, Secure, SameSite=Strict) creates a private sandbox; every query is scoped to it; no other visitor can see or act on its documents.
2. Optional: the visitor pastes their own list of 5–20 records (unit names/addresses/tenant names) so matching is real; otherwise Docket shows what it read and what it would do against the sample building.
3. Upload a PDF (PDF only, <=10 MB, <=20 pages, ~5 documents per sandbox per hour, plus per-address limits). They see extracted fields, the rules' pick and why, the model's suggestion, and can approve/dismiss within their sandbox.
4. Everything (PDF bytes, extracted text, their record list, workflow state, model exchange records) deleted after 1 hour or on "Delete now"; document text never written to logs; page states plainly that the document is sent to the model provider to be read, not kept, not used for training.
5. The existing $1/day global model budget and kill switch stay; limits are invisible until hit ("try again tomorrow"). The current server-made samples remain as "or try one of ours".
6. The invite code, the sign-in page and the shared queue go away.

## DECISIONS IN PLAY
1. Is this design safe enough to put on the public internet with no sign-up, and what specifically is missing (threats: hostile PDFs against pypdf, resource exhaustion/DoS of a shared VPS, cost abuse, sandbox isolation/IDOR, prompt injection, stored XSS via extracted text or pasted records, privacy/retention of real personal data in leases, abuse as free file hosting or as a proxy to the model, CSRF, bot floods)?
2. Which controls are must-have for v1 versus over-built for a demo (e.g. a CAPTCHA/Turnstile, PDF sanitising or rendering in a separate process/container, page/size/time limits, disk quotas, retention mechanics, legal/privacy copy)?
3. Should the uploaded PDF be shown back to the visitor (viewer) at all, and how, safely?
4. Is the "paste your own record list" input safe and worth it, or is there a simpler way to make a real document produce a meaningful result?

## READ FIRST
- /Users/cortega/Documents/Projects/docket/docket-trunk/docket/api.py (routes; /documents, /documents/{id}/file, /review, /beta/*, /reviewer/session)
- /Users/cortega/Documents/Projects/docket/docket-trunk/docket/beta.py and beta_samples.py, beta_reset.py
- /Users/cortega/Documents/Projects/docket/docket-trunk/docket/core/extraction.py, candidates.py, storage.py
- /Users/cortega/Documents/Projects/docket/docket-trunk/docket/activities.py, workflows.py
- /Users/cortega/Documents/Projects/docket/docket-trunk/docket/llm/budget.py, prompts.py
- /Users/cortega/Documents/Projects/docket/docket-trunk/docs/operations.md (hosted beta mode, runbook)
- /Users/cortega/Documents/Projects/docket/docket-trunk/docker-compose.yml
