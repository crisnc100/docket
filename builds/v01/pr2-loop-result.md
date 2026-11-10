All checks pass. Nothing staged or committed.

- `./verify.sh`: **556 passed**, zero skips.
- `./verify.sh --fault`: **561 passed**, zero skips.
- Both seed reports reproduce unchanged, with **220 unchanged replay keys each**.
- Seed-11 floors PASS: wrong files **0**; clean auto-file **10/10**; injection-to-injected-ID **0**; recall **38/39**, equal to baseline.
- Corpus, reports, and recordings are unchanged. f1/f3 remain filed.

All benchmark inputs have 64,000 characters, 2,000 distinct lines, and 400 records. After timings include coverage.

| Case | Before | After |
|---|---:|---:|
| Realistic text | 1.148s | 0.138s |
| Address whitespace | 1.085s | 0.103s |
| Street separators | 1.082s | 0.100s |
| Repeated street keywords | 1.077s | 0.100s |
| `#1 ` lines | 1.270s | 0.319s |
| Timer during seven activities | 8.185s | 0.062s |

**(a) New-test files**

- [test_matching_batch.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_matching_batch.py): benchmark, Unicode basenames, score equivalence.
- [test_shortlist_responsiveness.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/workflow/test_shortlist_responsiveness.py): concurrent activity timer and thread checks.
- [matching_inputs.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/matching_inputs.py): seeded fixtures.

**(b) Existing-test changes**

- [test_structural_matching_bounds.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_structural_matching_bounds.py): 200k/400k inputs become 32k/64k total characters; the token-boundary input becomes exactly 64k. Added `not over_budget` assertions. The 0.5s limit, 2.5× ratio, and address assertions remain.
- [test_matching_input_budget.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_matching_input_budget.py): all four attacks now use distinct lines. Added the distinct-line count assertion; existing limits remain.

**(c) Fix files**

- [activities.py:84](/Users/cortega/Documents/Projects/docket/docket-v01/docket/activities.py:84): loads records and builds the shortlist through `asyncio.to_thread`.
- [candidates.py:221](/Users/cortega/Documents/Projects/docket/docket-v01/docket/core/candidates.py:221): prepared inputs and RapidFuzz batch comparisons; basename extraction precedes NFKC.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`; branch `v01`; base `c988348`; [local service](http://127.0.0.1:8010/healthz).