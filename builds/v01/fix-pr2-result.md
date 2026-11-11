**f4 needs a corpus decision.** Seed 7 has 29 strong-tier documents. Every one contains the exact configured address and tenant name; all score `0.9500000`. Continuous similarity keeps exact matches at `1.0`, so the frozen corpus still permits only two distinct filing points.

I recommend adding corpus variants with graded name/address errors. Define the generation rule and threshold using seed 7, then evaluate seed 11 unchanged by its results. This requires new replay keys and operator-supplied answers.

May I add those variants and export only their new requests?

[Checkpoint and evidence](builds/v01/pr2-f4-callback.md). f3 remains untouched. No existing tests changed, no evals or verification scripts ran, and no commits were attempted.