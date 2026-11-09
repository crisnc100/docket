**Verdict: 1 PASS, 1 FAIL (narrow).** This is from reading the source and tests only; I had no shell in this session, so no pytest run and no mutation run.

| # | Changed assertion | Verdict | Evidence |
|---|---|---|---|
| 1 | `test_candidates.py:26` fixture `UNIT-0001-extra` → `UNIT-0001extra` | **PASS** | `candidates.py:10` lookahead `(?![^\W_])` rejects an alphanumeric after the id, so dropping or narrowing the right boundary to digits-only makes this fixture match and the test fail. |
| 2 | `test_scoring.py:97-98` threshold `"0.01"` → `config.strong_threshold` | **FAIL** | The override is now a no-op (`scoring.json:12-13` both 0.55), so the test only runs the default config and no longer probes any threshold below strong. |

**1 — not weakened.**
- The old fixture is now a real match: `-` is a valid boundary under the new pattern, and `test_pr1_findings.py:35-41` asserts that positively.
- The new fixture adds coverage the list lacked. The only other trailing-continuation case is `UNIT-00010` (a digit), so nothing previously guarded a trailing letter.

**2 — weakened, but only in the validation-bypassed region.** LOW 7/100 (likelihood 1/10 × impact 7/10).
- **What still holds:** the assertions at `:108-109` are unchanged. Deleting the conflict branch (`scoring.py:122-132`) still fails the test, because all five signals at 1.0 give a strong tier and 1 ≥ 0.55, so the decision would be "file".
- **What was lost:** the old input would catch a conflict path that reports `score == 0` but decides from a hidden value in [0.01, 0.55). The new input passes on that code.
- **Why it's narrow:** that bug cannot auto-file through a validated config, because `scoring.py:54` rejects a threshold below strong and `:143` requires the strong tier. It matters only when validation is bypassed.
- **Why I still call it FAIL:** the suite already treats the bypassed case as in scope, at `test_pr1_findings.py:97-102`.

**Fix:** keep the 0.01 by bypassing validation the same way that f3 test does. `Decimal` is already imported at `test_scoring.py:2`.

```python
low_threshold = config.model_copy(update={"auto_file_threshold": Decimal("0.01")})
```

Unverified — from `scoring.py:122-132` this should pass on current code, since the conflict branch returns before any threshold comparison. The original test name can come back with it, or this can sit alongside the minimum-valid-threshold variant.

The commit message describes both changes accurately.
