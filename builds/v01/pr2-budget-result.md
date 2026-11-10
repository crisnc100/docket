All checks pass. Nothing staged or committed.

- `./verify.sh`: **513 passed**, zero skips.
- `./verify.sh --fault`: **518 passed**, zero skips.
- Both seeds reproduce unchanged, with **220 unchanged replay keys each**.
- Seed 11: wrong files **0**; clean auto-file **10/10**; injection-to-injected-ID **0**; recall **38/39**, equal to baseline.
- Corpus, reports, and recordings remain unchanged.

At 64,000 characters, 2,000 lines, and 400 records:

| Attack | Before | After, with coverage |
|---|---:|---:|
| Address whitespace | 0.884s | 0.046s |
| Street separators | 0.885s | 0.045s |
| Repeated street keywords | 0.868s | 0.045s |
| `#1 ` lines | 1.500s | 0.244s |

Files for commits:

**(a) New tests only:** [core budget tests](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_matching_input_budget.py), [activity propagation test](/Users/cortega/Documents/Projects/docket/docket-v01/tests/workflow/test_matching_input_budget.py).

**(b) Existing-test change:** [test_pr1_findings.py:35](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr1_findings.py:35). Only the `/` case changes: its record-ID and filename-signal expectations become `candidates == ()`. This guards basename-only matching. The other seven separator cases retain both assertions.

**(c) Fix files:** [candidates.py](/Users/cortega/Documents/Projects/docket/docket-v01/docket/core/candidates.py), [scoring.py](/Users/cortega/Documents/Projects/docket/docket-v01/docket/core/scoring.py), [activities.py](/Users/cortega/Documents/Projects/docket/docket-v01/docket/activities.py). These enforce field budgets, preserve full explicit-ID scans, exempt filenames from token caps, use indexed address scanning, and route over-budget results to review.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`; branch `v01`; base `88bc193`; [local service](http://127.0.0.1:8010/healthz).