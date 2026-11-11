# ADR 006: require two independent signals for automatic filing

Status: accepted; written on 2026-11-11 after the measurements below.
This records an existing rule. No advance prediction is claimed here.

## Choice

A signal is evidence for a record, such as its address, tenant name or explicit id.
At least two independent signals must agree before a match can reach the strong tier.
A tier is a named evidence level: none, medium or strong.
An explicit id alone stays medium, even if repeated.
An address-derived building mention does not supply a second signal.

Strong evidence is necessary but not sufficient.
The score must also reach 0.5857142, selected on seed 7.
Evidence for different records forces review even when one candidate otherwise scores highly.
A document cannot instruct Docket to bypass those checks.

## Measurement

The [seed-11 report](../../evals/reports/2026-11-11-seed-11.json) has
zero wrong filings, 10/10 clean filings and zero filings to injected ids.
All 10 injection cases and all 10 two-record conflict cases go to review.
All 10 filename-id-only, 10 address-only, 10 tenant-only and 10 former-tenant cases also go to review.

These are outcomes under the rule, not a controlled comparison with the rule removed.
Tests separately show that one signal cannot become strong and conflicts override high scores.
Both the rules-only baseline and Docket apply this gate, so their equal filing results
cannot estimate the gate's advantage over a one-signal policy.
A one-signal auto-filing policy is not measured.

The cost is human work and missed coverage.
Docket sends 102/180 documents to review and files 38/39 expected filings.
[Document 0176](../failures/0176-threshold-miss.md) has the right record and a strong score
of 0.5842105, just below the selected threshold.
Seed 11 did not change that setting.

## Alternatives and limits

A lower threshold or one-signal filing policy in production: not measured.
The saved threshold sweep is a calculation over this sample, not a deployment trial.
It loses filings at higher thresholds without a measured precision gain.
Zero errors on synthetic documents is not a guarantee against real attacks.

## Evidence

- [Scoring tests](../../tests/core/test_scoring.py): single signals and conflicting records.
- [Independent building tests](../../tests/core/test_pr1_review_candidates.py).
- [Shipped threshold tests](../../tests/core/test_shipped_policy.py).
- [Seed-7 selection evidence](../../evals/threshold-selection.json).
