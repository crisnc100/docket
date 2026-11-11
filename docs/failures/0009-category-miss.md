# 0009: no candidate also means no category call

Document: [doc-0009-no_record.pdf](../../corpus/seed-11/doc-0009-no_record.pdf).
It begins `Notice document` and names `99999 Unlisted Boulevard, Elsewhere`.
The label names no record and expects review, category notice.

## What happened

Docket finds no candidates and calls neither model step.
It ends `would_review`, reason `no_candidate_chosen`, score 0 and tier none.
Its category is misc, so record abstention and routing are correct but classification is wrong.
The rules-only baseline assigns notice.

## Why

The workflow gives every empty shortlist category misc.
Classification is skipped along with record choice, even when the PDF contains useful category text.
Eight no-record documents account for the pipeline's eight additional category misses versus the baseline.
This is workflow policy, not a bad model response.

## What would fix it

Classify readable documents even when no record candidate exists, or use the baseline category rule there.
Either could preserve review routing while retaining the category.
The former adds calls and new replay keys; the latter changes the category policy.
Neither fix is measured here. They remain outside PR4.
