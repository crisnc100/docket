# ADR 008: real model answers from Claude Haiku 5.5

Status: accepted; measured. Predictions committed on 2026-11-12, before any live call.
The live pass ran on 2026-11-13 and missed gates G1 and G3. See Result.

## Context

Docket makes two model calls per document that reaches the model. One reads the
category. The other suggests a record to the human reviewer. In v0.1 every answer
was simulated: an operator supplied answers from a coding agent, standing in for
the target model ([ADR 004](004-simulated-model-measurements.md)). No figure in
v0.1 measures a real provider's answers, cost or latency.

The model does not decide automatic filing. The rules do: two independent signals
and the score threshold ([ADR 006](006-two-signal-rule.md)). That stays true here.

## Decision

Both calls go to Claude Haiku 5.5 (`claude-haiku-5-5`) on the Anthropic API,
through the official `anthropic` SDK.

- **Request:** the system prompt, the document in one user turn, and the per-call
  JSON schema as structured output. Thinking is off and effort is low. No sampling
  settings.
- **Fallback:** if the API refuses a schema, the process asks for JSON in the
  prompt instead. The gateway validates the answer either way. Each answer records
  which mode it used.
- **Cost:** tokens from the API's usage times the prices in
  [config/model_prices.yaml](../../config/model_prices.yaml). Each call reserves its
  worst case before it is sent.
- **Unreadable answers:** a refusal, a cut answer, text that is not JSON, or JSON
  that fails the schema. Each is charged, recorded with its reason and raw text,
  and raises the same error in replay.
- **Retries:** rate limits, overload, server errors and refused connections are
  retried four times. A timeout after sending is charged at its worst case and
  not retried.
- **Evidence:** one live pass over seeds 7 and 11, recorded in
  `evals/recordings/anthropic.jsonl`. CI replays it with no key. A report never
  mixes live and simulated answers.
- **Spend limits:** a $5 hard cap per run, a $2 run budget for the pass, and the
  daily ledger.

Haiku 5.5 is the only candidate. If the live pass misses a gate, the work stops for a
decision. It does not move to a larger model.

## Gates

- **G1:** at most 2% of the 440 requests are unreadable (at most 8).
- **G2:** at most 2% of requests get no answer after the one allowed resend.
- **G3:** seed 11 keeps the four v0.1 floors. No wrong filings. At least 90% of
  clean documents filed. No filing to an injected id. Recall at least the
  rules-only baseline's.
- **G4:** spend stays under the $5 hard cap.

## Predictions

Written before any live call. The Result section marks each one right or wrong.

| # | Prediction | Basis |
| --- | --- | --- |
| P1 | The API accepts both schemas as structured output. No fallback is needed. | Docket's schemas are smaller than Firstcall's, which the API accepted: an enum of five categories, and an enum of up to five record ids plus `none`. |
| P2 | At most 4 of 440 answers are unreadable (1%). At most 2 are refusals. | Enum outputs leave little room to go wrong. **Risk:** in the v0.1 simulation, Haiku inside a coding agent blocked 19 of 444 synthetic requests (4.3%). The direct API is set up differently, but this could miss G1. |
| P3 | The whole live pass costs at most $0.15. | 440 calls. A mean request is about 2,700 characters, so well under 1,500 input tokens with the schema. Outputs are a few tokens. |
| P4 | The mean cost per document that reaches the model is at most $0.0004. | Two calls per document, from P3. |
| P5 | The category is correct on at least 115 of the 120 seed-11 documents that reach the model. | The simulation got 120 of 120. |
| P6 | The suggestion is correct on 38 to 50 of the 102 seed-11 review documents. The rules stay at exactly 62 of 102. | Simulated Haiku got 44. The rules are deterministic. |
| P7 | Filing decisions match the rules-only baseline on all 180 seed-11 documents, unless a category failure fails a document. All four floors pass. | The model does not decide filing. |
| P8 | Median latency per call is at most 1.5 s, and the 95th percentile at most 4 s. | Short inputs, thinking off. |

## Spike

Run on 2026-11-12. The API accepted both enum schemas as structured output.
Measured by `uv run docket spike-models`:

```json
{
  "status": "PASS",
  "model": "claude-haiku-5-5",
  "provider": "anthropic",
  "cost_usd": "0.000073200",
  "calls": 2,
  "results": [
    {
      "enum": [
        "UNIT-0001",
        "none"
      ],
      "record_id": "UNIT-0001"
    },
    {
      "enum": [
        "UNIT-0199",
        "none"
      ],
      "record_id": "UNIT-0199"
    }
  ],
  "output_modes": [
    "json_schema"
  ]
}
```

## Result

One live pass on 2026-11-13 over seeds 7 and 11: 440 requests, each asked once, in
`evals/recordings/anthropic.jsonl`. The [seed-11 report](../../evals/reports/2026-11-13-seed-11.md)
and the [cost summary](../../evals/cost/2026-11-13-claude-haiku-5-5.json) hold every figure.

### Gates

| Gate | Result |
| --- | --- |
| G1 | **Missed.** 40 of 440 answers unreadable (9.1%), all refusals. |
| G2 | Passed. No request went unanswered. |
| G3 | **Missed on recall.** Seed 11 filed 28 of 39 expected documents; the rules-only baseline filed 38. No wrong filings, all 10 clean documents filed, no filing to an injected id. |
| G4 | Passed. The pass cost $0.0293; all of v0.2 PR2 cost $0.0320. |

Every refusal is on a `very_long` test document: both calls, on all 20 of them in the
two seeds. These documents hold about 8,000 characters of random synthetic words. The
API returned no text and `stop_details.category: "bio"`. A resend of the first ten was
refused again. No other case type was refused, including all 20 injection documents.
[Failure 0017](../failures/0017-very-long-refusal.md) gives the evidence.

The gates were not tuned and the pass was not repeated to improve them. One change was
made after the first refusals, by decision: an unreadable category answer now sends the
document to review instead of failing it. The rules, scores and threshold are unchanged.
Both seeds were then recorded. The ten seed-7 category refusals replayed from the archive,
as every answered request does; the record-suggestion calls they had blocked were asked
for the first time. The recall miss comes from that routing: the ten refused documents on
seed 11 wait for a person instead of filing.

### Predictions

| # | Result | Right? |
| --- | --- | --- |
| P1 | All 440 answers used structured output; no fallback. | Right |
| P2 | 40 unreadable, all 40 refusals. | Wrong |
| P3 | The pass cost $0.0293. | Right |
| P4 | $0.000122 per document that reached the model. | Right |
| P5 | Category right on 112 of 120. Every readable answer was right (110 of 110); the 10 refused fell back to misc, right on 2. | Wrong |
| P6 | Suggestion right on 43 of 102 review documents; rules right on 62 of 102. The 10 refused suggestions are excluded. | Right |
| P7 | Filing decisions differ from the baseline on 10 documents, all refused `very_long` documents sent to review. The recall floor fails. | Wrong |
| P8 | Median 1.33 s, 95th percentile 2.56 s over all 440 calls. | Right |

P2's stated risk is what happened. The v0.1 simulation's refusals were on the same
synthetic filler text, and the direct API refuses it too.
