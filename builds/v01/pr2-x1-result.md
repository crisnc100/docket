Reproduced x1 before changing code, then fixed both findings.

For `Address: 1 Cedar St` + 100,000 separator characters + `x`:

| Separator | Before | After |
|---|---:|---:|
| `.` | Stopped at 0.509s | 0.015s |
| `_` | Stopped at 0.506s | 0.025s |
| Space | 0.021s | 0.015s |
| Repeating `._ ` | Stopped at 0.508s | 0.052s |

The failing cases were interrupted by the 0.5-second deadline.

`UNIT_PATTERN` now prevents separator backtracking. Address tokens and sorted name chunks are computed once per chunk. Full text remains scanned, including late conflicting IDs. `unit_parts` also strips closing punctuation after removing `.pdf`.

Validation:

- **32 new cases pass**, including timing, continuous-score, late-conflict, and filename punctuation tests.
- `./verify.sh`: **479 passed**
- `./verify.sh --fault`: **484 passed**
- Zero skips. Seed 11 reproduced as PASS. No secret findings.
- Both seeds retain 220 replay keys. Corpus, results, recordings, and raw files are unchanged.

**Test-only commit:** [tests/core/test_pr2_unit_pattern_security.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_pr2_unit_pattern_security.py)

**Fix commit:** [docket/core/candidates.py](/Users/cortega/Documents/Projects/docket/docket-v01/docket/core/candidates.py)

No existing-test changes. No commits or index changes. Branch `v01`, HEAD `050b83f`.