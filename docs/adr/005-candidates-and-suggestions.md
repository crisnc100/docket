# ADR 005: restrict model choices and show them to reviewers

Status: accepted; written on 2026-11-11 after the measurements below.
This is a retrospective record, not a prediction committed before the experiment.

## Choice

The model may choose one of up to five shortlisted records or none.
Each request has a schema: a list of allowed fields and values.
Import and the model gateway check the answer against that request's allowed ids.
This bounds the answer to known candidates; it does not prove the selected candidate is right.

The later decision, approved on 2026-11-11, makes that choice a reviewer suggestion only.
Rules select the automatic filing record, score and tier.
The review page shows the model suggestion beside the rules' top candidate.
A choose failure records no suggestion and its reason, then continues with the rules.
Cancellation is propagated instead; it cannot fall through to scoring or filing.
Category classification remains a separate model call and can still fail the workflow.

## Measurement

The [held-out seed-11 report](../../evals/reports/2026-11-11-seed-11.json)
uses saved simulated answers.
The model's choose step changes zero filing decisions and zero filed records across 180 documents.
Both Docket and the rules-only baseline file 38 correctly, with zero wrong filings.
Their proposed records differ on 79 review items.

Among the 102 documents sent to review, the model is correct on 44 (43.1%,
95% Wilson interval 33.9–52.8%).
The rules' top candidate is correct on 62 (60.8%, interval 51.1–69.7%).
Suggesting none counts as correct when the expected label names no record.
Documents that did not go to review and failed suggestions are excluded.
The saved run has zero suggestion failures; a successful suggestion of none is an abstention.
Wilson intervals give uncertainty ranges for these proportions.

These results support keeping the suggestion out of automatic filing.
They do not show a reviewer works faster or makes fewer mistakes with the suggestion.
That human-use comparison is not measured.
The pipeline's category accuracy is 124/140 versus 132/140 for the rules-only baseline.
All 16 pipeline misses have empty shortlists, where the workflow assigns misc without a classify call.
They comprise eight no-record documents and eight documents without text layers.
The simulated classify answers are correct on all 120 documents whose shortlists were nonempty.
These counts include duplicate documents; they are not counts of unique model calls.
Record choice and pipeline category accuracy are separate measures.

## Alternatives and limits

Unrestricted model-generated record ids: not built, not measured.
Model-controlled automatic filing under the revised contract: not built, not measured.
A review page with only the rules' pick: reviewer time and accuracy not measured.
No accuracy improvement from the model's choice is claimed.

The simulated answers passed import checks, but that does not measure real-provider output compliance.
[ADR 001](001-model-enum-spike.md) keeps the real-provider prediction pending.
[ADR 004](004-simulated-model-measurements.md) describes the answer sources.

## Evidence

- [Approved role change](../../builds/v01/plan.md#p4p5-rev-2-replaces-p4-and-p5--2026-11-11).
- [Suggestion comparison](../../tests/test_review_comparison.py) separates suggestions from filing.
- [Workflow checks](../../tests/workflow/test_optional_suggestion.py) cover choose failures and retained review approval.
- [Gateway checks](../../tests/test_gateway.py) and [import checks](../../tests/test_exchange.py) reject invalid answers.
