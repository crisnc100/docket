# Step 0 — PASS, ready for a separate test-only commit

Commit these two paths only:

- `tests/core/test_scoring.py`
- `builds/v01/step0-done.md`

Added `test_conflict_overrides_even_maximum_score_with_bypassed_low_threshold`.
It uses `config.model_copy(update={"auto_file_threshold": Decimal("0.01")})`,
maximum positive evidence and conflict=True, and asserts raw_score=1,
score=0, decision=review, conflict=True and reason=conflicting_records.
The minimum-valid-threshold sibling remains unchanged. No assertion was removed
or loosened; production code, fixtures, config and verify.sh are unchanged at this
checkpoint. No commit was attempted.

```text
$ ./verify.sh
219 passed in 23.89s
PASS docket/core/candidates.py: 20/20 branches = 100.00% (required 95%)
PASS docket/core/scoring.py: 32/32 branches = 100.00% (required 95%)
PASS docket/core/ledger.py: 16/16 branches = 100.00% (required 95%)
PASS docket/core/storage.py: 20/20 branches = 100.00% (required 90%)
detect-secrets: {}
[exit 0]
```

Step 0 ends here. P3 work continues in the same session, in separate paths;
`tests/core/test_scoring.py` will not be edited further for P3.
