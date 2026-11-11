# PR2 scoring callback

Status: blocked on the corpus choice. No scoring or test files changed.

Seed 7 has 29 strong-tier documents. All score 0.9500000.
They comprise 10 clean documents, 10 exact duplicates and 9 long documents.
All 29 contain the exact configured address and current tenant name.
These counts came from extraction, shortlisting and scoring in this session.

```text
Seed 7 strong-tier score histogram: {'0.9500000': 29}
Seed 7 strong-tier case counts: {'clean': 10, 'exact_duplicate': 10, 'very_long': 9}
Canonical field exact matches: {'canonical_address_exact': 29, 'canonical_tenant_exact': 29}
Strong-tier documents: 29
```

Exact field matches have similarity 1.0. Continuous similarity therefore leaves
these scores equal. Any threshold sweep still has at most two distinct filing
points. Tenant matching already preserves fuzzy similarity above its cutoff.
Address matching currently accepts only normalized exact phrases.

Recommendation: allow corpus variants with graded name and address errors.
Choose their generation rule and the filing threshold on seed 7 only.
Apply that rule to seed 11 without tuning it there.
Keep the existing requests and answers. Export only the new requests for the
operator to answer. This requires permission to add replay keys.

Finding f4 remains blocked. Finding f3 has not been changed.
No replay keys were written. No models were called. No threshold was changed.
No existing tests were changed. The verification scripts were not run.
No commit was attempted.
