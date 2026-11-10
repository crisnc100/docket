Stopped: **two replay keys changed per seed**. Each seed still has 220 requests, with 218 unchanged. No requests were exported and no answers or recordings were written.

| Seed | Activity | Old key → new key |
|---|---|---|
| 7 | choose | `36d272d6aadad874104b5a620c79cf26b7dd0712b1dbb253ff4d2221d9302b1b` → `5474a32d22302171979adfc6d62fd1b5a4b37a241bea7a2ae829317e1dc2bfce` |
| 7 | classify | `e17a1030ec24ebc8c8b0712dd3b9912b050b8e17093be8f65b2b2596626b141c` → `67b9c92ff5953a3bf0126ef33d95ba6f4e756abfd80e335894f61ff4825bc9f4` |
| 11 | classify | `8bc6a2b8b94cb11c58cf4a0f57454996fc478b281e761160bad335d3182314b2` → `3728b8345222ecdb109f1f1c4c6f108084bc236ace77ece5f3ce36587c08052e` |
| 11 | choose | `c8320027f54edf2027ef007e7c422675a51a6d4ed518556321a20f1a10f9e901` → `ea2a64f32471bcc84d1528a79da81d2443d71714f54dbe17dd02507f47a9962e` |

The four conflicted files are marker-free. README keeps core’s structure and adds workflow/eval content; the log retains both sides. Nothing was staged or committed.

The tokenizer regressions first produced **23 failures and three passing controls**. Combined checks now pass: **213 tests, zero skips**, Ruff and strict type checking. The graded former-tenant case also has a failing-first eligibility fix.

**Changed existing test:** [tests/test_pr1_review_corpus.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/test_pr1_review_corpus.py:25). It retains the assertion for 170 original cases and additionally requires 180 total documents and ten of every case type.

**Pending after the required stop:** both replay evals, current seed-11 floors, `./verify.sh` and `./verify.sh --fault`. Saved reports remain unchanged and are labelled as pre-merge measurements.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · branch `v01` · base `829dd3e` · API URL `http://127.0.0.1:8010`.