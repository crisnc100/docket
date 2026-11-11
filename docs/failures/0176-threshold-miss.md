# 0176: the correct record falls just below the threshold

Document: [doc-0176-graded_evidence.pdf](../../corpus/seed-11/doc-0176-graded_evidence.pdf).
It says `Record: UNIT-0131` and `Address: 1610 zlm Street Apt 11`.
The altered street text is deliberate test data.
The label expects a lease filed to `UNIT-0131`.

## What happened

Both the rules and the model name `UNIT-0131`.
Docket assigns category lease and a strong score of 0.5842105.
It ends `would_review`, reason `insufficient_corroboration`.
That means observe mode would send it to a person without filing.
The selected threshold is 0.5857142, so this is the one missed expected filing.

## Why

Two signals establish a strong tier, but the address match is weaker than an exact address.
The weighted score misses the filing threshold by 0.0015037.
The tier alone cannot authorize filing.
The rules-only baseline misses the same document.

## What would fix it

Human approval can file this document under the existing policy.
A lower threshold would include it in the saved sweep, but changing the threshold
because of this held-out result would defeat the test split.
Collect more representative errors, select any new policy on tuning data and test it on a fresh held-out set.
That policy change is not measured.
