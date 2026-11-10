All seven findings are fixed. No commits or index changes.

- `./verify.sh`: **447 passed**
- `./verify.sh --fault`: **452 passed**
- Archive `--check`: **444 recordings match**
- Zero skips. Seed 11 reproduced as PASS. No secret findings.
- Before the fixes, 17 new regression cases failed.

| Finding | Fix and new test |
|---|---|
| s1 | Linear label scan; 128k-space input takes ~0.019s. [Deadline test](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_pr2_review_candidates.py:10) |
| 2 | Strip closing brackets and quotes from units. [Unit endings](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_pr2_review_candidates.py:28) |
| 3 | Reuse recordings without working files. [Missing folders](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr2_review_fixes.py:22) |
| 4 | Merge archives, preserve keys, reject mismatches before atomic replacement. [Archive tests](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr2_review_fixes.py:54) |
| 5 | Require a measured PASS that reproduces. [Gate test](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr2_review_fixes.py:122) |
| 6 | Separate live-document budgets; retain shared daily and evaluation caps. [Budget tests](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr2_review_fixes.py:139) |
| 7 | Default shadow diff to newest seed 11. [Selection test](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr2_review_fixes.py:202) |

**(a) New tests for the test-only commit**

```text
tests/core/test_pr2_review_candidates.py
tests/test_pr2_review_fixes.py
```

**(b) Existing-test change**

`tests/test_exchange_archive.py`: setup only. The secret-scanner fixture’s recording now matches its altered answer. No assertions changed; AST comparison confirms this. It still requires detection of the unknown secret in decompressed evidence after working folders are removed.

**(c) Fix files**

```text
docket/core/candidates.py
docket/llm/exchange.py
docket/llm/archive.py
docket/runtime.py
docket/cli.py
scripts/reproduce_eval.py
evals/recordings/README.md
```

Both seeds retain 220 replay keys. Corpus, results, archive, recordings, and all 888 raw files are unchanged.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`, branch `v01`, HEAD `642bf5a`.