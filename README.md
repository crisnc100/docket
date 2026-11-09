# docket

Docket matches incoming documents to property records and files them or sends them to review.

The current results use simulated model responses. They are not OpenRouter measurements.
The saved responses comprise 422 Haiku responses and 18 Opus responses.
The evaluations replay operator-supplied answers and make zero external model calls.

The rules-only baseline chooses the record with the highest score without a model.
The simulated choose step changes no filing/review decision and no filed record on either seed.
A review proposal is a suggested record for the reviewer.
It changes review proposals on 74 documents in seed 7 and 79 in seed 11.
Both sets of documents still go to review. This run shows no filing gain from model choice.

The filing threshold is 0.5857142, selected on seed 7 only.
Seed 11 is the held-out test set. It selects no settings.
It has zero wrong filings, 90% clean filing and zero filings to injected ids.
Recall is the share of expected filings found correctly.
Recall is 36/39 (92.31%), equal to the rules-only baseline.
Each eight-point threshold curve has six distinct precision/rate pairs.
Precision is the share of automatic filings matching the expected record, category and decision.
It stays at 100% when there are filings. Higher thresholds reduce the share filed here.

Category accuracy is the share of non-rejected documents with the expected category.
It is 124/140 (88.57%) for Docket and 132/140 (94.29%) for the baseline on both seeds.
The difference comes from eight documents per seed with no candidate records.
Those documents receive misc without a model call.

The [seed-7 report](evals/reports/2026-11-09-seed-7.md) and
[seed-11 report](evals/reports/2026-11-09-seed-11.md) include the curves,
category results, confidence intervals and simulator provenance.
