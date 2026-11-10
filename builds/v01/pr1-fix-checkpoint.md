# PR1 grader fix checkpoint

One fix round complete for f1, f2, f3, f4, f5 and f7. f6 remains blocked on
Cristian's OpenRouter key, as instructed. No review, grade or live model was run.
All changes are uncommitted on `v01`, based on `408b01a`; no commit was attempted.

## Findings, changes and proving tests

All proving tests below are in `tests/test_pr1_findings.py`. The original
regression set was run before production edits: 13 failures and 6 passes. Every
fixed finding had at least one failure. That original set passed after the fixes;
seven additional CLI cases bring the final regression module to 26 passing tests.

| Finding | Result | Change (file:line) | Proving regression test |
| --- | --- | --- | --- |
| f1 — 40/100 (8/10 x 5/10) | FIXED | `docket/core/candidates.py:10`: ID boundaries accept hyphens, underscores and other non-alphanumeric delimiters; alphanumeric continuations remain excluded. | `test_f1_filename_separators_match_ids` (line 36), `test_f1_corpus_filename_only_ids_are_real_candidates` (line 45): both seeds' filename-only IDs resolve; mismatched body IDs conflict. |
| f2 — 45/100 (9/10 x 5/10) | FIXED | `docket/corpus.py:128` and `:131`: address-only and tenant-only labels become review. Filename-only and former-tenant-only already use review. Both `corpus/seed-7/labels.jsonl:3` and `corpus/seed-11/labels.jsonl:3` regenerated. | `test_f2_generator_labels_every_single_signal_as_review` (line 61), `test_f2_committed_single_signal_labels_match_strict_scoring` (line 70); existing `tests/test_corpus.py:39` regeneration test still passes for both seeds. |
| f3 — 28/100 (4/10 x 7/10) | FIXED | `docket/core/scoring.py:54`: config validation rejects auto-file below strong with Pydantic ValidationError; `:143`: filing requires Tier.STRONG and the threshold. | `test_f3_config_load_rejects_auto_file_threshold_below_strong` (line 88), `test_f3_filing_requires_strong_even_if_config_validation_is_bypassed` (line 97). |
| f4 — 40/100 (10/10 x 4/10) | FIXED | `docket/cli.py:49`: `docket score <pdf>` reads, extracts, shortlists and scores every candidate; chooses the highest deterministic score; prints JSON candidates/signals/score/tier/auto-file recommendation. No LLM or filing effect. | `test_f4_score_cli_runs_rules_only_pipeline_on_corpus_pdf` (line 105): real corpus PDF and an LLM-call trap; checks highest-score choice and required output fields. Review/empty/missing-file cases at lines 180 and 193. |
| f5 — 27/100 (9/10 x 3/10) | FIXED | `docket/core/extraction.py:15`: corrupt reason; `:30`: typed DocumentRejection; `:39`: DocumentParseError is its subtype; `:72`: malformed PDFs raise it with reason corrupt, status rejected and non-retryable. `docket/core/ledger.py:55` allows corrupt. `builds/v01/plan.md:39` reflects that rejection reason. CLI catches the typed rejection. | `test_f5_corrupt_pdf_raises_typed_rejection` (line 126), `test_f5_ledger_accepts_corrupt_as_terminal_rejection` (line 138), `test_f5_score_cli_reports_poison_documents_as_rejected` (line 169). |
| f6 — 30/100 (10/10 x 3/10) | BLOCKED — unchanged | `docs/adr/001-model-enum-spike.md:3` stays PENDING. No spike result or provider-compliance claim was fabricated. | Live experiment deliberately not run; blocked on Cristian's key. Existing offline gateway/spike tests pass. |
| f7 — 9/100 (3/10 x 3/10) | FIXED | `compose.yaml:45`: SeaweedFS probe now requests `http://localhost:8333/healthz` on the S3 gateway. | `test_f7_compose_healthcheck_probes_running_s3_gateway` (line 148): checks the configured target and executes that exact probe inside the real container. |

## Red/green evidence

```text
$ uv run pytest tests/test_pr1_findings.py
[before production fixes]
FAILED ...test_f1_filename_separators_match_ids[-]
FAILED ...test_f1_filename_separators_match_ids[_]
FAILED ...test_f1_corpus_filename_only_ids_are_real_candidates[7]
FAILED ...test_f1_corpus_filename_only_ids_are_real_candidates[11]
FAILED ...test_f2_generator_labels_every_single_signal_as_review
FAILED ...test_f2_committed_single_signal_labels_match_strict_scoring[7]
FAILED ...test_f2_committed_single_signal_labels_match_strict_scoring[11]
FAILED ...test_f3_config_load_rejects_auto_file_threshold_below_strong
FAILED ...test_f3_filing_requires_strong_even_if_config_validation_is_bypassed
FAILED ...test_f4_score_cli_runs_rules_only_pipeline_on_corpus_pdf
FAILED ...test_f5_corrupt_pdf_raises_typed_rejection
FAILED ...test_f5_ledger_accepts_corrupt_as_terminal_rejection
FAILED ...test_f7_compose_healthcheck_probes_running_s3_gateway
13 failed, 6 passed in 1.14s

[after fixes, original regression set]
19 passed in 1.48s
```

## Final validation

```text
$ ./verify.sh
All checks passed!
51 files already formatted
0 errors, 0 warnings, 0 informations
collected 218 items
tests/test_pr1_findings.py ..........................
218 passed in 20.96s
PASS docket/core/candidates.py: 20/20 branches = 100.00% (required 95%)
PASS docket/core/scoring.py: 32/32 branches = 100.00% (required 95%)
PASS docket/core/ledger.py: 16/16 branches = 100.00% (required 95%)
PASS docket/core/storage.py: 20/20 branches = 100.00% (required 90%)
detect-secrets: {}
[exit 0]
```

Core extraction also covers 14/14 branches. Both existing corpus regeneration
checks pass without changing their text/label determinism assertions. Real
SeaweedFS storage tests remain enabled; no happy path was mocked.

```text
$ uv run docket corpus generate --seed 7
Generated 170 documents: corpus/seed-7
$ uv run docket corpus generate --seed 11
Generated 170 documents: corpus/seed-11

Seed 7: 170 docs; 40 single-signal labels; all review=True
Seed 11: 170 docs; 40 single-signal labels; all review=True

$ uv run docket score corpus/seed-7/doc-0001-clean.pdf
[JSON output includes]
chosen_record_id: UNIT-0083
score: 0.9500000
tier: strong
would_auto_file: true
```

`docker compose up -d --wait seaweedfs` returned Healthy after applying the probe.
A direct S3 gateway request returned HTTP/1.1 200 OK from localhost:8333/healthz.
`git diff --check` passes.

## Deliberate choices and test expectation updates

- No scoring weight, strong threshold, two-signal requirement or clean-case floor
  was weakened. Auto-file below strong is now invalid. No tuning was performed.
- The operator's pre-existing clean-case >=90% floor edit was preserved. The only
  additional plan edit lists corrupt among rejection reasons.
- Filename IDs use Unicode-aware alphanumeric boundaries; underscore is treated
  as a delimiter despite being a regex word character.
- `tests/core/test_candidates.py:26` changes the obsolete negative example
  UNIT-0001-extra to UNIT-0001extra. The negative assertion still guards an ID
  joined to an alphanumeric suffix. New f1 positives cover the newly required
  hyphen/underscore behavior and actual corpus filenames.
- `tests/core/test_scoring.py:97` retains the maximum-score conflict-to-review
  assertion, using the minimum *valid* auto-file threshold (strong_threshold)
  instead of the now-forbidden .01 config. New f3 tests separately require a typed
  load error and enforce strong-tier filing even after bypassing validation.
- Those are the only existing test expectation changes; all other assertions and
  coverage/zero-skip gates remain intact. No tests were deleted or skipped.
- Both seeds were regenerated. Twenty encrypted fixtures produced random byte
  changes despite identical decrypted text; their original bytes were preserved
  after asserting decrypted page-text equality. Only the intended label changes
  remain in the corpus diff; text determinism still passes.
- `DocumentParseError` remains available as a subtype for existing callers, while
  its base DocumentRejection makes terminal rejection semantics explicit. CLI and
  ledger tests prove rejected:corrupt; no P3 workflow was added.
- The score command only reports what the rules would do. It does not call a
  model, create a document row, transition a status or write an S3 object.

## Outstanding

Only f6: the live model spike needs Cristian's key. No further fix round, model
review, commit, push or PR was performed. No other implementation question is open.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`; branch `v01`;
base `408b01a`. No app server URL. Local S3: `http://127.0.0.1:9000`.
