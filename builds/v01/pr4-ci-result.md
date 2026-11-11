Both fixes are complete, uncommitted.

- Review opens before database publication, with a new Temporal patch guard. The deterministic regression fails on old code; old and new histories replay.
- The benchmark retains its one-second limit using CPU time. Controls verify that scheduling delays are ignored and excess CPU work still fails.
- `./verify.sh`: **632 passed, zero skips**. Seed-11 replay PASS; secret scan clean.

[Result and grouped file/assertion inventory](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/pr4-ci-result.md). No pushes, reviews or other models ran.