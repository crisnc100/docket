Done. All checks pass. No commits or index changes.

[exchange.jsonl.gz](/Users/cortega/Documents/Projects/docket/docket-v01/evals/recordings/exchange.jsonl.gz) preserves 444 request/answer pairs, exact source bytes, hashes, and provenance: **425 Haiku / 19 Opus**. Repacking produces identical bytes. Secret scanning includes decompressed contents.

| Check | Evidence |
|---|---|
| Archive `--check` | PASS: 444 recordings match answers and provenance |
| `./verify.sh`, folders present | PASS: 427 tests |
| `./verify.sh`, folders renamed aside | PASS: 427 tests |
| `./verify.sh --fault` | PASS: 432 tests |

All runs had zero skips, reproduced seed 11, and reported `detect-secrets: {}`. Both raw folders are restored, with 444 files each. All 1,265 protected files remain byte-for-byte unchanged, including corpus and eval results.

**Test-only commit**

```text
tests/test_graded_evidence.py
tests/test_exchange_archive.py
```

The existing graded-evidence test reads saved requests from the archive. **No assertions changed.** It still guards original file hashes, 200 requests per seed, valid request digests, exact regenerated-request equality, and replay-key presence. New tests cover archive integrity, determinism, missing folders, recording mismatches, and secret detection.

**Main commit**

```text
.gitignore
docket/llm/archive.py
scripts/pack_exchange.py
scripts/check_secrets.py
verify.sh
docs/rerecord.md
evals/recordings/README.md
evals/recordings/manifest.json
evals/recordings/exchange.jsonl.gz
```

Include your cached removals of the 888 raw files in that commit. They remain on disk and are still tracked until you run `git rm --cached`.

No open questions or deviations. Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`, branch `v01`, HEAD `f2e5994`.