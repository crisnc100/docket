# 0002: the model abstains when the filename names the record

Document: [doc-0002-UNIT-0200-id_in_filename_only.pdf](../../corpus/seed-11/doc-0002-UNIT-0200-id_in_filename_only.pdf).
The record id appears only in the filename.
The label expects review for an inspection about `UNIT-0200`.

## What happened

The rules' top candidate is `UNIT-0200`.
The model suggests none, an incorrect abstention for this label.
Docket still selects the rules' record, scores 0.05 with tier none,
and ends `would_review`, reason `insufficient_corroboration`.
The category is inspection, as expected.
Review is required here; the missing suggestion is the error.

## Why

The shortlist reads the filename.
The model's choose request includes the extracted body text and candidate ids, not the filename evidence.
The single candidate's presence does not tell the model why it was selected.
The saved answer says none; the simulator's internal reason is not recorded.
This input gap is a possible explanation, not a proven account of its reasoning.

## What would fix it

The reviewer can approve the rules' pick now.
Passing the filename and structured candidate evidence to the model might reduce these abstentions.
It would change request keys and need new answers and a fresh comparison.
That prompt change is not measured and would not remove the two-signal filing gate.
