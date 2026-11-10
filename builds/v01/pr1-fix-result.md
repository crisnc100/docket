All seven issues are fixed on `core`, uncommitted.

- `./verify.sh`: **249 passed, zero skips**.
- 30 new regression cases; failing-first runs reproduced 22 failures. **No existing tests changed.**
- Both corpus determinism tests pass.
- All four published service ports bind to `127.0.0.1`.
- Scoring code and policy are unchanged. Only two tenant fields and three PDFs changed.

[Checkpoint with each finding, file:line and test](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/pr1-review-fix-checkpoint.md).

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01` · branch `core` · base `942ba4c` · [Temporal UI](http://127.0.0.1:8233).