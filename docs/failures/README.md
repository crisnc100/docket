# Evaluation failures

These are seed-11 results from the live answers of claude-haiku-5-5, recorded on 2026-11-13.
A gold label is the expected record, category and decision supplied with a test document.
The [report](../../evals/reports/2026-11-13-seed-11.json) stores every result;
[labels](../../corpus/seed-11/labels.jsonl) supply the expected answers.
There are no wrong automatic filings in this sample.
Errors below include refused answers, a missed filing, an absent suggestion, a wrong category
and a wrong rules pick. Notes 0176, 0002, 0009 and 0075 were first written from the v0.1
simulated answers; each was checked against the live report.

| Document | Failure |
| --- | --- |
| [0017: very_long refusals](0017-very-long-refusal.md) | The API refuses both calls on all 20 very_long documents; they go to review |
| [0176: threshold miss](0176-threshold-miss.md) | Correct record, but an expected filing goes to review |
| [0002: filename id](0002-missing-suggestion.md) | Rules find the record; the model suggests none |
| [0009: unlisted address](0009-category-miss.md) | Correct review routing, wrong category |
| [0075: decoy address](0075-decoy-address.md) | Rules and the model both propose the neighbouring unit |

Proposed remedies are not implemented or measured.
They need tests and new evidence before becoming policy.
A held-out failure is not permission to tune against seed 11.
