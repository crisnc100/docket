# PR1 review fixes

All seven issues pass their regression checks on branch `core`.
The review file has six headings. Its corpus f2 heading covers two separate failures.
No commit, push, model call, review or grade was run.

| Finding | Status | Code | Regression test |
| --- | --- | --- | --- |
| candidates f1: different unit corroborates unit 1 | PASS | `docket/core/candidates.py:20` keeps whole unit identifiers after Apt, Apartment, Unit or #. Hyphens between address words remain separators. | `tests/core/test_pr1_review_candidates.py:11`, `test_different_unit_cannot_corroborate_shipped_unit_one`; 1-B and 1-2 failed before. 1A is a control. All three remain ID-only review cases. |
| candidates f2: address-derived building counts twice | PASS | `docket/core/candidates.py:134` removes address phrases before checking for a separate building mention. | `tests/core/test_pr1_review_candidates.py:36`, `test_address_derived_building_is_not_independent`; four cases failed before. Canonical addresses, aliases and repeated addresses supply only one independent signal. |
| gateway f1: client output exceptions escape ParseError | PASS | `docket/llm/gateway.py:138` maps output and schema exceptions to non-retryable ParseError. Line 145 settles valid reported usage. | `tests/test_pr1_review_gateway.py:25` and `:87`; six wire-response cases and two schema-error cases failed before. All eight pass. |
| corpus f2: empty former tenants | PASS | `docket/corpus.py:97` finds eligible records. Line 122 selects a replacement when needed. | `tests/test_pr1_review_corpus.py:25`, parameter `empty_former`; failed before with IndexError. It generates all 170 documents with UNIT-0094's former tenants cleared. |
| corpus f2: a building has only one unit | PASS | `docket/corpus.py:98` groups neighbours. Line 124 selects a record with a neighbour. | `tests/test_pr1_review_corpus.py:25`, parameter `single_unit_building`; failed before with IndexError. It generates all 170 documents with UNIT-0055 in its own building. |
| records f1: shared names cause conflicts | PASS | `config/records.csv:198` and `:199`; `scripts/unique_tenants.py:9` deterministically renames cross-unit duplicates. | `tests/test_pr1_review_corpus.py:63` and `:72`; uniqueness and both affected unit checks failed before. Both units file with agreeing evidence. |
| compose s1: published ports accept network traffic | PASS | `compose.yaml:27` binds both Temporal ports to 127.0.0.1. Postgres and SeaweedFS already use that address. | `tests/test_pr1_review_compose.py:5`; failed before. It checks every published port in the resolved compose config. Runtime container inspection also confirms all four bindings. |

ParseError is non-retryable: Temporal must not repeat that call.
Truncated and filtered responses settle their reported cost when it is valid.
Missing or invalid cost keeps the reservation, since billing is unknown.
Neither failure writes a recording. Timeout handling and bounded 429 retries are unchanged.
The tests use local HTTP responses. No external model was called.

The building signal remains available for candidate ranking.
It contributes to scoring only when a building mention survives removal of the address.
Separate mentions in body text, subject or filename still count.
`test_building_mentioned_outside_address_remains_independent` guards those three cases.
Hyphen-joined filenames retain conflict detection in all three sources.
Scoring code, weights, thresholds and candidate ordering are unchanged.

If no record can supply former tenants or neighbours, generation raises ValueError before writing files.
`test_corpus_rejects_missing_case_eligibility_before_writing` covers both cases.
Eligible shipped records keep the original random selection sequence.

## Corpus and names

The name script uses a local Faker instance seeded with zero.
It keeps the first owner of a name and reserves all existing names before replacements.
Only two fields changed: UNIT-0197's current tenant is Norma Fisher;
UNIT-0198's former tenant is Jorge Sullivan.
The helper test checks repeatability, a second unchanged run and preservation of other fields.
The CSV retains its existing CRLF line endings.

Both corpus seeds were regenerated with 170 documents each.
Only these three PDFs changed:

- `corpus/seed-11/doc-0035-clean.pdf`
- `corpus/seed-11/doc-0045-exact_duplicate.pdf`
- `corpus/seed-7/doc-0068-very_long.pdf`

Each now has no conflict and files at score 0.9500000.
Labels and filenames are unchanged. Exact duplicate bytes still match their clean source.
The 20 existing encrypted PDFs were retained after comparing their decrypted text with regeneration.
Both unchanged corpus determinism tests pass across all 170 documents per seed.

Reproduction commands:

```sh
uv run python scripts/unique_tenants.py
uv run docket corpus generate --seed 7
uv run docket corpus generate --seed 11
docker compose up -d --wait
./verify.sh
```

## Validation

The initial regression run had 20 failures and seven passing controls.
The two additional schema-error tests also failed before implementation.
The completed regression files have 30 passing tests, including the name helper check.

`./verify.sh` exited zero: **249 passed in 24.13s**, with zero skips.
Ruff, strict core pyright, corpus determinism and secret checks pass.
Branch coverage: candidates 25/26 (96.15%); scoring 32/32, ledger 16/16 and storage 20/20 (100%).
Storage tests use real SeaweedFS.
Postgres, Temporal and SeaweedFS are healthy. Their four published ports bind only to 127.0.0.1.
The Temporal UI returns HTTP 200.

Changed existing tests: **none**. Four new test files contain the regression checks.
No test or check was removed, skipped or relaxed. No dependency was added.
No P3 code or replay data was changed. No open questions remain.
Command evidence is in `pr1-review-fix-evidence.txt`; local logs are in `.cache/pr1-review-fix/`.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`.
Branch: `core`. Change base: `942ba4c`.
Local URL: `http://127.0.0.1:8233` (Temporal UI).
