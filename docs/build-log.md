# Building v0.1

This journal was written on 2026-11-11 from git history and saved build evidence.
Dates below are the dates of that work, not dates when this journal was committed.
A seed fixes generated test data. A held-out seed is reserved for evaluation, not tuning.
Model measurements below use simulated answers unless stated otherwise.

## 2026-11-09 — define what would count

The [initial plan review](../builds/v01/bounce.md) found gaps in the proposed filing claims.
An injected record id could win without corroboration.
Duplicate handling did not yet cover later resubmissions, observe mode could collide with real work,
and crashes while accepting uploads had no recovery test.
A curve made from three fixed tiers would reveal little.

The revised [plan](../builds/v01/plan.md) required two independent signals,
unique filing effects, separate observe runs, bounded retries and explicit terminal reasons.
It fixed seed 7 for tuning and seed 11 for the held-out check.
Commit `c97253a` saved the plan and review; `ac20871` added the foundations.

## 2026-11-09 — build the matching core

Commit `408b01a` added extraction, candidate matching, two-signal scoring, storage and the ledger.
The [first grade](../builds/v01/grade-pr1.json) caught a filename id that disappeared beside a hyphen,
a filing path that checked score without requiring strong evidence,
and a health check that did not test the S3 endpoint.
Commit `354fd5c` fixed those paths and added the score CLI.

Later address checks found that unit 1 could be confused with 1A,
and a strong match outside the five selected candidates could escape conflict detection.
Commits `f497aaf` and `a90a3e2` tightened record identity.
[Saved checks](../builds/v01/address-token-checkpoint.md) include failing-first examples.

## 2026-11-09 — make work survive a crash

Commit `75be810` added the Temporal workflow, review waits, reconciliation and the fault suite.
The five process/storage cases checked recovery with a single filing effect.
The [P3 checkpoint](../builds/v01/p3-checkpoint.md) records the crash assertions and captured histories.

No OpenRouter key was available.
The operator approved export/import instead: code exported requests and the operator supplied simulated answers.
The first 400 answers came from Haiku and Opus, with provenance saved per answer.
These results measured the workflow with supplied answers, not the real provider.
[ADR 004](adr/004-simulated-model-measurements.md) records that boundary.

## 2026-11-09 — make the curve useful

The [PR2 grade](../builds/v01/grade-pr2.json) found that every strong filing scored 0.95.
Eight thresholds therefore produced only two distinct outcomes.
The [callback](../builds/v01/pr2-f4-callback.md) asked for graded name/address errors.
The operator approved a shared generation rule, saved in `85669b2` before the new held-out measurement.

Commit `24093d1` added continuous match strengths and ten graded documents per seed.
The threshold selection used seed 7 only and chose 0.5857142.
The operator supplied 40 new answers.
Commit `cb053f5` saved the held-out results: six distinct curve points, with no filing gain from model choice.
The model changed review proposals and category labels, not filing decisions.

## 2026-11-10 — review the core and preserve evidence

Core review found that a building name inside an address was being counted as independent evidence.
It also found output-parser errors that did not become named, non-retryable errors and ambiguous synthetic tenant names.
Commit `86a1c12` fixed these and bound published ports to localhost.
PR1 merged as `ac20f15`.

After the accepted tenant corrections, four changed model requests needed replacement answers.
The operator supplied them; old recordings remained intact.
Commit `bb416e7` saved the new results: seed 11 filed 38/39 expected documents,
including 10/10 clean cases, with zero wrong filings.
The threshold stayed unchanged.
[Reimport evidence](../builds/v01/reimport-four-checkpoint.md) separates old and current requests.

Commit `642bf5a` put original requests and answers into one compressed evidence archive.
Replay no longer depended on untracked working folders.
The importer and secret checks still validated the archive contents.

## 2026-11-10 — stop patching matching one input at a time

Matching took four review/fix rounds and a cross-model consult to reach one input budget.
Early fixes addressed repeated separators, repeated street prefixes and long unit tokens
(`050b83f`, `7e02f1e`, `88bc193`).
Each new adversarial input exposed work that the earlier timing tests had missed.

The [budget review](../reviews/2026-11-10-19-43-11-6de8d4/code-review.md) records the four-model agreement.
Commit `c988348` set field budgets before matching:
64,000 body characters and 2,000 lines, 1,000 subject characters and a 255-character filename.
Over-budget fields force review rather than being silently shortened.
The complete explicit-id scan remains, so a late contradictory id cannot disappear.
The [budget results](../builds/v01/pr2-budget-result.md) record the checks.

That still left an event-loop problem.
The event loop is the worker's shared task scheduler.
A review test with 2,000 distinct lines delayed concurrent activities;
repeated identical lines in the earlier test had hidden the cost.
Commit `03d6e9f` moved shortlisting to a thread and compared approximate text matches in batches.
The [saved benchmark](../builds/v01/pr2-loop-result.md) reports a timer delay falling
from 8.185 seconds to 0.062 seconds across seven activities.
The final normal and fault runs passed 556 and 561 tests with zero skips.
PR2 merged as `a9287a8`.

## 2026-11-11 — make uncertainty reviewable

The operator changed the model's role before PR3: suggestions for the reviewer only.
The [plan revision](../builds/v01/plan.md#p4p5-rev-2-replaces-p4-and-p5--2026-11-11)
split the review page and docs into PR3 and PR4.
The saved seed-11 comparison found correct suggestions on 44/102 review documents,
versus 62/102 for the rules' top candidate.
No new simulation was needed.

Commits `69ffef2` and `fc98439` added the signed webhook, review API, PDF preview, CLI and page.
Security review found reviewer routes needed access control.
Commit `fd30fc2` required a shared Bearer token, kept it in browser session storage,
and loaded assets and PDFs through authenticated requests.
The [fix results](../builds/v01/pr3-fix-result.md) record 604 normal and 609 fault tests passing.
PR3 merged as `43d4825`.

## 2026-11-11 — PR4 documentation and filed fixes

The README gives the document path, quickstart, held-out curve, intervals and limits.
The measured ADRs say they were written after the experiments.
Failure notes separate safe review routing from a wrong proposed record or category.
No prediction was backdated.

Choose failures leave no suggestion, record a reason and continue to rules scoring.
A patch guard preserves the old failure path when replaying an older workflow history.
Extraction, classification and filing failures retain their terminal failure behavior.
Login clears a saved token only after HTTP 401; service and network failures retain it.
The browser harness checks the browser result before copying artifacts.
The report's suggestion section excludes every document not sent to review, as its code already did.

Reports were generated through the existing replay path with no new answers:

```sh
uv run docket eval --seed 7 --mode replay --budget 0
uv run docket eval --seed 11 --mode replay --budget 0
```

PR4's validation results and existing-test assertion changes are recorded in
[the builder result](../builds/v01/pr4-result.md).
