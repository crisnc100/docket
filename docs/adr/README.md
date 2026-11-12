# Decision records

An architecture decision record (ADR) states a choice, its evidence and its limits.

| Record | Evidence and status |
| --- | --- |
| [001: per-call allowed ids](001-model-enum-spike.md) | Original real-provider prediction preserved; result pending |
| [002: workflow command changes](002-review-workflow-patching.md) | Current histories replay; future deployment prediction unmeasured |
| [004: simulated answers](004-simulated-model-measurements.md) | Saved simulation and its limits |
| [005: candidates and reviewer suggestions](005-candidates-and-suggestions.md) | Written after measurement; 44/102 versus 62/102 |
| [006: two independent signals](006-two-signal-rule.md) | Written after measurement; safety results and missed filing |
| [007: Temporal recovery](007-temporal-recovery.md) | Written after measurement; five process/storage fault cases |
| [008: real model answers](008-real-model.md) | Predictions P1–P8 committed before any live call; result pending |

The original plan's D15 asked for predictions committed before measurements.
The three new measured records are retrospective and say so in Status.
They do not claim to satisfy that timing requirement.
Their choices were already implemented when this documentation was written.
The PR4 brief permits this treatment; the existing Prediction text is unchanged.
