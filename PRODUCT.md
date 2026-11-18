# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Hiring managers and business leaders or owners.** Cristian is looking for applied-AI and forward-deployed roles, and offers his services as an AI consultant. These visitors decide in a few minutes whether he builds AI systems that can be trusted with real paperwork. They are the main audience of the public front page.
- **Property-operations staff** who file incoming leases, inspections, invoices and notices, and the reviewers who settle uncertain matches. They use the review page every day: the PDF, why it waits, the candidate units with their scores, the rules' top pick and the model's suggestion.

## Product Purpose

Docket is an open-source pipeline that reads an incoming property document (a PDF) and matches it to a known unit, called a record, such as `UNIT-0001`. It files the document automatically only when two independent pieces of evidence agree, the score reaches the threshold, and nothing points at a second unit. Everything else waits for a person on the review page. Every step leaves a trace.

Success on the front page is a visitor who has watched a real test document get read, matched, scored, and then either filed or sent to a person, and who understands why. Success on the review page is that a reviewer can settle a document quickly and nothing is filed to the wrong unit.

## Positioning

The rules decide; the model only suggests. Automatic filing needs two independent signals (such as an address and a tenant name), a strong score at or above 0.5857142, and no conflict. A language model labels the category and may suggest a record to the reviewer, but it never decides whether or where a document files. In the held-out test its suggestions were less accurate than the rules, and the project publishes that. A document that only names a unit id cannot file itself, which is the main defence against injected instructions.

## Operating Context

- A PDF arrives by upload (`POST /documents`) or a signed email webhook (`POST /inbound`), 10 MB limit. Docket saves the original bytes, deduplicates exact copies, and starts a Temporal workflow that survives a worker crash.
- Steps for each document: read the text; reject unreadable files with a reason (`zero_byte`, `too_large`, `unsupported_type`, `encrypted`, `corrupt`); find up to five candidate units; ask the model for a category and an optional suggestion; score the evidence; file, or send to review.
- Signals: record id in the text, record id in the file name, address (spelling variants count), tenant name (current or former, close spellings count), building code.
- Review reasons seen in the test data: `conflicting_records`, `insufficient_corroboration`, `no_candidate_chosen`, `category_unreadable`, `input_too_large`.
- A reviewer approves the rules' pick, the model's suggestion or another candidate, or dismisses with a reason. Unanswered reviews expire.
- `uv run docket score <pdf>` prints candidates, per-signal evidence, score, tier and decision with no services and no model key. `uv run docket status <id>` prints a document's trace.
- Not yet built: a public front page (this redesign adds one, running from recorded results in the browser, nothing uploaded), and a hosted "try your own PDF" mode, planned as a separate later PR. The front page must leave a place for it.

## Capabilities and Constraints

- Model: Claude Haiku 5.5 on the Anthropic API, recorded live once on 2026-11-13; replays use saved answers with no model call.
- Data: synthetic. 200 units; seeds 7 and 11 each generate 180 test documents across case types (clean, address only, tenant only, former tenant, two-record conflict, decoy near-miss address, building level, no record, injection, exact duplicate, empty text layer, corrupt, encrypted, zero byte, non-PDF, very long, id in file name only, graded evidence). Seed 7 chose the threshold; seed 11 was held out.
- A document whose unit is not in the list finds no candidate. It goes to review with reason `no_candidate_chosen`, gets category misc, and the model is not called. Docket never creates a record from document text.
- No optical character recognition: a scan with no text layer finds no candidate and goes to review the same way.
- Conflicting evidence and single-signal matches go to review. This keeps filing precision at the cost of more human work.
- The model's safety filter refused every `very_long` test document (category `bio`). They go to review instead of failing; this is the whole recall gap against the rules-only baseline.
- The local deployment is a development example, with one shared reviewer token; it is not a measured production service or a per-user permissions system.
- Terminology to translate for visitors: record, signal, tier, threshold, seed, held-out, gold label, rules-only baseline, precision, recall, Wilson interval, observe mode, Temporal, workflow.

## Brand Commitments

- The name Docket. Nothing visual is binding yet.
- It must look clearly different from Cristian's two other projects: Askwell's "Audited Ledger" (sage cheque paper, guilloche, indigo carbon, red rubber stamp, Archivo) and Firstcall's "On the Rail" (brushed steel rail, thermal-paper tickets, age stripes, Big Shoulders, Martian Mono, Atkinson Hyperlegible Next).
- Plain language, not salesy, no AI-startup look, not busy. Less is more.

## Evidence on Hand

- Held-out report, seed 11 (`evals/reports/2026-11-13-seed-11.md`, `.json`): Docket filed 28 of 180 automatically, all 28 correct, none wrong; recall 28 of 39 against the rules-only baseline's 38 of 39; 112 of 180 sent to review; 40 rejected as unreadable. Model suggestions were right on 43 of 102 review items, the rules' top pick on 62 of 102. Every interval is a 95% Wilson interval.
- The live pass missed two of its four gates (unreadable answers 40 of 440, 9.1%, against a 2% limit; recall against the baseline). Both are published in the README and ADR 008.
- Cost and speed: $0.0293 for the whole 440-call pass; median call 1.29 s on seed 11.
- Failure notes in `docs/failures/`: 0017 (very long refusals), 0176 (threshold miss by 0.0015), 0002 (id in file name only), 0009 (unlisted address), 0075 (decoy address, where the rules and the model both propose the neighbouring unit).
- Fault tests: five crash and outage cases, each ending in one document and one filing (ADR 007).
- Per-document recorded results for every seed-11 document live in the JSON report; `docket score` reproduces the candidate scores.
- All figures measure synthetic documents, not real mail. No customers, testimonials, case studies or production deployments exist. Never invent them.

## Product Principles

- Show the mechanism, not a claim: a visitor watches a real test document get read, matched and decided.
- The rules decide; the model suggests; a person settles the rest. Make that split visible every time a decision appears.
- Waiting for a person is a correct outcome, not a failure. Show review as the safe path.
- Publish the misses beside the wins. Every number traces to a committed report.
- Plain language first; research terms live behind a fold. Less is more.

## Accessibility & Inclusion

Assumed from the brief, not confirmed: WCAG 2.2 AA; the demo must be fully usable by keyboard and must respect reduced motion, with every animated step also readable as static text.
