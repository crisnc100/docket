# Graded evidence rule

This rule is written before measuring seed 11 with graded scores.
It applies to both seeds. Seed 11 will not select any settings.
The operator will commit this file. The implementer does not commit.

Append ten documents of case type `graded_evidence` after the existing 170.
Use a separate random stream seeded with the corpus seed.
Choose ten records from the records config with that stream.
Do not consume the existing generator's random stream.
Keep existing bytes, labels, names and document ids unchanged.
Cycle through the five categories twice.
Every new document contains its exact record id.

Apply these ten profiles in order:

| Profile | Address | Tenant | Explicit building | Gold decision |
| --- | --- | --- | --- | --- |
| 0 | St and # abbreviations | Current, exact | Yes | filed |
| 1 | One street-name letter changed | Current, exact | Yes | filed |
| 2 | One street-name letter changed | Current, one letter changed | No | filed |
| 3 | Exact | Current, last name first | No | filed |
| 4 | Unit number removed | Absent | No | review |
| 5 | One street-name letter changed | Absent | No | filed |
| 6 | Absent | Current, one letter changed | Yes | filed |
| 7 | Absent | Former, one letter changed | Yes | filed |
| 8 | St and # abbreviations | Current, last name first | No | filed |
| 9 | Unit number removed | Current, one letter changed | Yes | filed |

A letter change replaces the first alphabetic letter with z, or x if it is z.
For an address, change the street name, never a number.
Name reversal reverses the space-separated words.
These changes do not introduce a second record identity.
Profile 4 lacks a second unit-specific signal and must go to review.

Keep candidate admission, order and conflict detection unchanged.
Grade the selected candidates after selection.
Exact ids, normalized exact addresses and exact names have strength 1.
Address similarity uses RapidFuzz ratio after address normalization.
House and unit numbers must agree exactly. A missing unit gives strength 0.
Name similarity is the larger of ratio and token-sort ratio.
Token-sort ratio compares names after sorting their words.
Use the existing 0.88 cutoff for both fuzzy fields.
Do not infer a nickname relationship.
Keep weights, tier thresholds and the two-signal and conflict rules unchanged.
Weights, tiers and fuzzy cutoffs remain untuned defaults.

Select the filing threshold from seed-7 rules-only scores.
Require zero wrong filings and at least 90% clean automatic filing.
Maximize correct filings. Break ties with the largest threshold.
Consider 0.55, each eligible score, and 1.0.
Save the selection evidence before exporting seed 11.
The model comparison remains pending until the operator supplies answers.

Preserve existing request files and answers.
Check all original request keys before exporting anything.
Only new requests without an existing recording may be written.
Export no gold label or profile metadata in a model request.
