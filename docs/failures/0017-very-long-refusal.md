# 0017: Haiku's safety filter refuses every very_long document

Documents: the ten `very_long` documents in each seed, for example
[doc-0017-very_long.pdf](../../corpus/seed-11/doc-0017-very_long.pdf).
Each has a short header (record id, address, building, tenant), then about 8,000
characters of random English words, then `End of report.` It holds no instructions.
The label expects automatic filing.

## What happened

Both model calls were refused on all 20 `very_long` documents across seeds 7 and 11.
That is 40 of the 440 live requests (9.1%). The API answered with `stop_reason: refusal`,
no text and zero output tokens. Its `stop_details.category` was `bio`. The first ten
category refusals on seed 7 were recorded before Docket kept the category. A diagnostic
resend of those ten (`builds/v02/refusal-resend.jsonl`, kept out of the archive and the
gates) was refused again with category `bio`, so the refusal is deterministic.

No other case type was refused. All 20 `injection` documents were answered.

Docket now sends a document with an unreadable category answer to review, with reason
`category_unreadable` and category `misc`. The rules still score it: on seed 11 all
ten have a strong match that the rules alone would file. None files automatically.
That costs recall: 28 of 39 expected filings on seed 11, against the rules' 38 of 39.

## Why

The request shape is the same for every document: the same system prompt, schema,
256-token limit, thinking off and effort low. The 110 other category calls on seed 11
were answered. The only difference is the long run of random words, which the API's
safety classifier labels `bio`. The v0.1 simulation saw the same pattern: Haiku
blocked 19 synthetic requests that held filler text.

This is the model's classifier reacting to Docket's synthetic filler text, not
a fault in the request. Real long documents are not random words. Whether real documents
trip the same classifier is not measured.

## What would fix it

Docket keeps the model and prompt unchanged. The refusal is safe: the document waits
for a person instead of failing or filing.

Options not built or measured:

- Generate `very_long` fixtures from realistic text instead of random words. This
  changes the corpus, so it needs a new held-out seed.
- Send the category call a bounded excerpt instead of the whole long text.
- Use the rules' category when the model refuses. This changes the category policy.
- A fallback model on refusal, as the API's message suggests. This is out of scope:
  Haiku 5.5 is the only model.
