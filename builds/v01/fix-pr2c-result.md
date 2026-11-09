Both seeds **PASS** at the unchanged **0.5857142** threshold. Results are simulated: **422 Haiku / 18 Opus** recordings.

Seed-11 floors all pass:

- Wrong filings: **0**
- Clean filing: **9/10, 90%**
- Filings to injected IDs: **0**
- Recall: **36/39, 92.31%**, equal to baseline

Both eight-point curves have **six distinct points**. Docket and baseline filing curves match. Precision stays 100% wherever filings occur; higher thresholds reduce the share filed.

Category accuracy on both seeds: **124/140, 88.57%**, versus baseline **132/140, 94.29%**.

**Choose changes no filing/review decision or filed record on either seed.** Review proposals differ on 74/79 documents; those documents still go to review.

Validation: **313 passed** normally; **318 passed** with faults. Zero skips; required branch coverage 100%. The shipped-config table was added. No existing tests changed. No commits made.

[Checkpoint, full curves and evidence](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/graded-import-checkpoint.md).

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · `v01` · base `24093d1` · [API health](http://127.0.0.1:8010/healthz).