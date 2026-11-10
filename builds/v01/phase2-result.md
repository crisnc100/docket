P2 is **incomplete, stopped on the scoring-policy choice**.

Built extraction, candidates, real-S3 storage, ledger, and the coverage gate. **149 tests pass with zero skips**; candidates, ledger, and storage each have **100% branch coverage**. `verify.sh` correctly fails because scoring is missing.

The plan leaves scoring numbers and independence rules unspecified. Recommended configurable starting policy:

- ID/address/tenant/building/filename weights: `.30/.30/.25/.10/.05`.
- Medium `.25`; strong `.70`; auto-file `.70`.
- Repeated IDs count as one independent signal.
- An address-derived building adds no independent signal.
- Conflicts always force review; one signal never reaches strong.

These are unmeasured defaults for later seed-7 tuning.

**Approve that policy, or specify alternatives?**

[Full checkpoint: each exit check, command evidence, deviations](/Users/cortega/Documents/Projects/docket/docket-v01/builds/v01/p2-checkpoint.md)

Changes remain uncommitted in `docket-v01`, branch `v01`, base `ac20871`. No commit attempt occurred.