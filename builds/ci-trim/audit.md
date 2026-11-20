# Test and CI audit (read-only, 2026-11-20). Approved by Cristian: "please do it".

## Delete (34 tests)
- A. core/test_pr2_unit_pattern_security.py::test_street_followed_by_100000_separators… (16 params). Dead since MAX_BODY_CHARS = 64000 (docket/core/candidates.py:27): text over the budget becomes "" before any regex runs (:310, :317). The same prefixes and separators are timed inside the budget at 32k and 64k, with not over_budget asserted, in core/test_structural_matching_bounds.py:29-55.
- B. core/test_pr2_review_candidates.py::test_address_with_128000_spaces… (1). Same reason; ("Address:", " ", "x") is covered at 64k in test_structural_matching_bounds.py:34.
- C. core/test_pr2_unit_pattern_security.py::test_long_separator_run_does_not_hide_a_late_conflicting_id (1). core/test_matching_input_budget.py:49-76 asserts the same thing and also checks the decision.
- D. test_matching_cpu_budget.py (2). It tests the SIGPROF timer of another test's harness.
- E. test_pr4_regressions.py::test_browser_failure_preserves_artifacts_and_primary_error (4). It tests the browser test's teardown helper, with docker mocked.
- F. test_temporal_environment.py (1). It tests the SDK's time-skipping clock, which every workflow test already relies on.
- G. core/test_pr1_review_candidates.py::test_hyphen_joined_address_still_conflicts_with_another_id (3). A strict subset of core/test_address_unit_tokens.py:10-26.
- H. test_pr1_findings.py::test_f7_compose_healthcheck_probes_running_s3_gateway (1). Every CI job's compose up --wait seaweedfs fails if the probe fails.
- I. test_pr1_findings.py::test_f2_generator_labels_every_single_signal_as_review (1). Implied by test_corpus.py:43 together with test_pr1_findings.py:72-88.
- J. test_pr1_findings.py::test_f3_filing_requires_strong_even_if_config_validation_is_bypassed (1). Covered by core/test_scoring.py:130-140.
- K. test_reimported_reports.py::test_corpus_guard_changes_only_the_three_accepted_tenant_payloads and ::test_seed7_revalidation_preserves_threshold… (2). They audit a one-off migration; the current state is guarded by test_graded_evidence.py:100-110 and :151, and core/test_scoring.py:247-259.
- L. test_scaffold.py (1). It pins "docket 0.1.0" for a scaffold command no doc mentions.
- M. workflow/test_optional_suggestion.py::test_choose_failure_records_reason_and_preserves_rules_route[review-retryable-retries_exhausted-5] (1 param, 15.6 s of real backoff). [file-retryable…] covers exhausting retries, and the other 3 error types cover the review route.

## Merge (minus 4 tests)
- Unit-one identity, 5 tests to 3: add "1-B" to the parametrize of core/test_conflict_address_regressions.py::test_x2_shipped_unit_one_cannot_gain_address_support… (:55-67), then delete core/test_pr1_review_candidates.py::test_different_unit_cannot_corroborate_shipped_unit_one.
- test_pr1_findings.py::test_f5_corrupt_pdf_raises_typed_rejection: move its 3 extra assertions (DocumentRejection, status, reason) into core/test_extraction.py:40-47, then delete it.
- test_spike.py:7 and test_anthropic_spike.py:21 become one test parametrized by provider.
- test_secret_scan.py:27-46 and test_live_secret_scan.py:20-42 become one, if one fixture can serve both archive sources (unverified). If it can't cleanly, skip this merge and say so.
- No count change: fold test_f3_config_load… in as a row of core/test_scoring.py:228-244, and fold test_merged_corpus.py into test_pr1_review_corpus.py:24.

## CI
1. Fault tests run on push to main, nightly (schedule) and workflow_dispatch, not on pull_request. Update coverage.needs, the job list in scripts/check_ci_results.py, and its collected count, which today counts tests/ including fault.
2. Delete the second coverage floor ("with the fault tests"). Coverage can only add branches and the floors are identical (scripts/check_coverage.py:9-14).
3. Delete scripts/check_ci_results.py and its step: 13 s on the critical path. Every upload already uses if-no-files-found: error, and a shard with 0 tests exits 5.
4. Keep: the eval replay, pack_exchange --check, the 5 fault tests (moved off PRs, not deleted), the 2 browser tests, test_key_hygiene, test_pr1_review_compose, test_beta_gate, test_record_resume, test_cost_summary, test_record_run_provenance, the coverage-gate and secret-scan tests, the replay-gate tests in test_pr2_review_fixes.py, test_report_wording, and optional_suggestion [file-retryable].
