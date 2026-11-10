Both findings reproduced before editing and are fixed structurally.

Matching now bounds tokens to **64 characters** and lines to **8,192 characters** before regex processing. Cut values retain a marker so truncation cannot create a valid shorter unit. The explicit-ID scan still reads full text.

Whitespace no longer joins single letters to unit numbers. Both sentence examples pass; adjacent `1A` and `1-A` remain valid.

| Timing | 200k chars | 400k chars |
|---|---:|---:|
| Before: `st.`, `st-`, `dr.`, `rd-`, `ln.` | Hit 0.5s deadline | Hit 0.5s deadline |
| After: worst median across all 15 generators | 0.0306s | 0.0338s |

Largest doubling ratio: **1.46×**, below 2.5×.

Validation:

- `./verify.sh`: **490 passed**
- `./verify.sh --fault`: **495 passed**
- Zero skips; coverage floors met.
- Seed 11 floors PASS and reproduce.
- Both seeds retain 220 replay keys. Saved data is unchanged.

**Test-only commit:** [tests/core/test_structural_matching_bounds.py](/Users/cortega/Documents/Projects/docket/docket-v01/tests/core/test_structural_matching_bounds.py)

**Fix commit:** [docket/core/candidates.py](/Users/cortega/Documents/Projects/docket/docket-v01/docket/core/candidates.py)

No existing-test changes, commits, or index changes. Branch `v01`, HEAD `7e02f1e`.